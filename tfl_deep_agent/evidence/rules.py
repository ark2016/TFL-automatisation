"""Verdict-gate predicates of the evidence ledger, with no package dependencies (OO-023).

Pure functions over ledger-shaped dicts: the IR fingerprint, the fingerprint rule and the
Lean proof side. ``ledger.py`` re-exports them, so ``from .ledger import ir_admissible``
keeps working.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def canonical_json(obj: Any) -> str:
    """Deterministic JSON (sorted keys, no spaces, non-ASCII kept)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def ir_fingerprint(ir: Any) -> str:
    """A short stable id of an IR (sha256 of its canonical JSON), so a report
    can say which IR a check was made against ("проверено относительно IR …")."""
    return "ir-" + hashlib.sha256(canonical_json(ir).encode("utf-8")).hexdigest()[:12]


def entry_fingerprint(entry: Mapping[str, Any]) -> str | None:
    """The IR a record of the gate's shape was made against: what its result names,
    else the IR it was called with; ``None`` when it says nothing about an IR."""
    data = entry.get("data") or {}
    if isinstance(data.get("ir_fingerprint"), str):
        return data["ir_fingerprint"]
    ir = (entry.get("args") or {}).get("ir")
    return ir_fingerprint(ir) if isinstance(ir, Mapping) else None


def ir_admissible(entry: Mapping[str, Any], required: str | None) -> bool:
    """The verdict gate's fingerprint rule: when an IR is required, a record made
    against another one does not count (a record that names no IR does)."""
    return not required or entry_fingerprint(entry) in (None, required)


def lean_proof_side(entry: Mapping[str, Any], lang: str, *, required_fingerprint: str | None = None) -> str | None:
    """The side of the verdict a ``lean_check`` record of the gate's shape proves for a
    task of class ``lang``, or ``None`` when the gate would not count it.

    R-Lean (docs/VERDICT_POLICY.md): a kernel-checked proof of the statement code
    generated from this task's IR (``lean_statement``) is a verified basis for its side;
    the tool gives ``verified`` only for status ``proved`` with standard axioms. Class ``ll``
    has no Lean path. ``required_fingerprint``: the IR the gate requires, or ``None``."""
    data = entry.get("data") or {}
    side = data.get("side")
    counted = (entry.get("tool") == "lean_check" and entry.get("ok") and lang in ("reg", "cfl", "dcfl")
               and data.get("cls") == lang and data.get("status") == "proved" and entry.get("trust") == "verified"
               and side in ("constructive", "destructive") and ir_admissible(entry, required_fingerprint))
    return str(side) if counted else None
