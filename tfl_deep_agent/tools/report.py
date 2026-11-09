"""Report tools: ``list_report_templates`` and ``submit_report`` (P3).

``list_report_templates`` is an ordinary registry tool (group ``report``,
trust always ``not_verified``: a template is formatting, not evidence).

``submit_report`` is **not** a registry tool: its ToolMessage artifact is the
contract the agent's report gate reads, ``{"tool": "submit_report", "ok":
true, "report": {...}}`` (``agent._is_submitted_report``), not a
``ToolResult``. :func:`make_submit_report_tool` builds it over
:class:`SubmitReportArgs`, a superset of the P2 schema in ``agent.py`` (every
P2 report still validates), so the agent can switch to it without touching
the mock scripts. HITL 2 interrupts on the tool name (``interrupt_on``), the
renderer (``reports/render.py``) turns the accepted fields into
``result.{json,md,html}``.

The schema gives the model no channel for trust: ``SubmitReportArgs`` derives
from ``ToolArgs`` (``extra="forbid"``), and no field is named ``trust`` /
``confidence`` / ``verified`` (checked by ``ToolArgs``' reserved names in the
tests). Theorem ids are checked against docs/THEORY.md, so a citation always
resolves.

Evidence ids (2026-11-08): when the tool has the run's evidence ledger
(``make_submit_report_tool(evidence=...)``, the agent passes it), a report
citing an id that is not in the ledger -- in ``evidence_ids``,
``claims[].evidence_ids``, ``steps[].evidence_ids`` or ``lean.evidence_ids``
-- is NOT accepted: the tool returns an error result (status ``error``, no
artifact, so no report is recorded) that lists the unknown ids and the valid ids grouped by tool name with their trust and IR
fingerprint (:func:`evidence_id_error`), and asks for a resubmission with the
exact ids. A tool name is never resolved to an id by code. The renderer and
the verdict gate still lower trust for an id without a record (a run without a
ledger, a report rated outside a run): that is the second line of defence.

HITL 2 skips the refusals it can foresee (``hitl.report_interrupt_config``): invalid
fields, unknown ids when it has the ledger, leaked tool-call markup. The tool refuses
those straight away and the model resubmits, with no card. A report refused for
unknown ids with no ledger in that config still stops first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from ..evidence.ledger import cited_evidence_ids
from ..reports.render import (
    SELECTABLE_TEMPLATES,
    TEMPLATE_INFO,
    TEMPLATES_DIR,
    load_template,
    template_placeholders,
)
from .base import ToolArgs, ToolResult, ToolUnsupported, tfl_tool

__all__ = [
    "SUBMIT_REPORT",
    "EvidenceId",
    "ListReportTemplatesArgs",
    "ReportClaim",
    "ReportLean",
    "ReportLemma",
    "ReportStep",
    "SubmitReportArgs",
    "TemplateName",
    "TheoremId",
    "cited_evidence_ids",
    "evidence_id_error",
    "list_report_templates",
    "make_submit_report_tool",
    "report_args_models",
    "unknown_evidence_ids",
    "validate_report",
]

SUBMIT_REPORT = "submit_report"

TemplateName = Literal["full_proof", "exam_solution", "short_verdict", "lean_appendix"]
EvidenceId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
TheoremId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=24,
                                             pattern=r"^(§\s*)?[A-Za-z0-9.]+$")]

_EVIDENCE_IDS_DESC = (
    "ids of the tool calls whose results support this: the exact tool_call_id of each call (e.g. "
    "toolu_...), as in the evidence ledger, never a tool name. No id: the claim is printed as resting "
    "on the model's words only; an id with no ledger record makes the report be refused.")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportClaim(_Strict):
    """A key claim of the proof with the tool results behind it."""

    text: str = Field(min_length=1, max_length=4000,
                      description="the claim, e.g. 'a^(3i+2) b^i ∈ L for every i ≥ 0'")
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=40, description=_EVIDENCE_IDS_DESC)


class ReportStep(_Strict):
    """One step of the proof (P2 fields ``claim`` / ``justification`` /
    ``evidence_ids`` plus ``title`` and ``theorem_ids``)."""

    title: str = Field(default="", max_length=200,
                       description="short step name, e.g. 'Предположение', 'Выбор слова', 'Накачка'")
    claim: str = Field(min_length=1, max_length=4000,
                       description="the statement of this step, e.g. 'the DFA accepts exactly L'")
    justification: str = Field(default="", max_length=8000,
                               description="the argument for the step (lemma used, case analysis)")
    theorem_ids: list[TheoremId] = Field(default_factory=list, max_length=10, description=(
        "docs/THEORY.md section ids (from list_theorems) this step uses; code prints the citation"))
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=40, description=_EVIDENCE_IDS_DESC)


class ReportLemma(_Strict):
    """A theory statement the proof uses, cited by section id."""

    theorem_id: TheoremId = Field(description="docs/THEORY.md section id from list_theorems, e.g. '2.1'")
    use: str = Field(default="", max_length=1000,
                     description="how the statement is applied here (do not restate the lemma)")


class ReportLean(_Strict):
    """The Lean block. Status and axioms are read from the cited lean_check
    ledger entries by code, never from this block."""

    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=20,
                                           description="ids of the lean_check calls")
    proof_path: str | None = Field(default=None, max_length=500, description="path of the proof file")
    note: str = Field(default="", max_length=2000, description="a short note on the formalization")


class SubmitReportArgs(ToolArgs):
    """The structured report. Trust, confidence and the evidence block are
    computed by code; there is no field for them."""

    verdict: str = Field(min_length=1, max_length=200, description=(
        "the answer, e.g. 'regular', 'not regular', 'CFL', 'not CFL', 'DCFL', 'not DCFL', 'LL(1)', "
        "'not LL(1)', 'not LL(k) for any k', or 'unknown' when you could not settle it. Code maps it "
        "to the policy token: reg regular / non_regular / partial, cfl cfl / non_cfl / null, "
        "dcfl dcfl / non_dcfl / null, ll ll / not_ll / uncertain ('unknown' is the last of each)"))
    answer: Literal["yes", "no", "inconclusive"] | None = Field(default=None, description=(
        "the direction of the verdict: yes = the language (grammar) IS in the class asked, "
        "no = it is NOT, inconclusive = not settled. Give it whenever the verdict text is not a "
        "plain 'X' / 'not X'; a disagreement with the verdict text makes the verdict inconclusive"))
    language_class: Literal["reg", "cfl", "dcfl", "ll"] = Field(
        description="the class the task asks about: reg, cfl, dcfl or ll")
    method: str = Field(min_length=1, max_length=2000, description=(
        "the proof method, e.g. 'construction: regex -> minimal DFA', 'pumping lemma for CFL', "
        "'LL(1) table without conflicts'"))
    hypothesis: str = Field(default="", max_length=4000,
                            description="the hypothesis and why this method was chosen (full_proof)")
    lemmas: list[ReportLemma] = Field(default_factory=list, max_length=20,
                                      description="theory statements used, by docs/THEORY.md section id")
    claims: list[ReportClaim] = Field(default_factory=list, max_length=40,
                                      description="the key claims of the proof, each with its evidence ids")
    steps: list[ReportStep] = Field(min_length=1, max_length=60, description="the proof as ordered steps")
    construction: str = Field(default="", max_length=4000, description=(
        "the chosen word, grammar, automaton or table summary (printed by exam_solution)"))
    conclusion: str = Field(default="", max_length=4000,
                            description="the closing paragraph; empty: summary is printed instead")
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=200, description=(
        "tool-call ids supporting the verdict as a whole (in addition to the per-step ids)"))
    template: TemplateName = Field(default="full_proof", description="report template to render")
    summary: str = Field(default="", max_length=4000,
                         description="two or three sentences: hypothesis, method, conclusion")
    ir_fingerprint: str | None = Field(default=None, max_length=64, description=(
        "fingerprint of the IR the oracle results refer to (validate_ir data.ir_fingerprint)"))
    lean: ReportLean | None = Field(default=None, description=(
        "the Lean block when a Lean proof was checked (lean_check ids, proof path, note)"))

    @model_validator(mode="after")
    def _theorem_ids_resolve(self) -> SubmitReportArgs:
        ids = [lem.theorem_id for lem in self.lemmas] + [t for s in self.steps for t in s.theorem_ids]
        if not ids:
            return self
        from .theory import load_theory

        try:
            doc = load_theory()
        except ToolUnsupported:
            return self  # no checkout: the renderer marks the ids it cannot resolve
        bad = []
        for t in dict.fromkeys(ids):
            try:
                doc.resolve(t)
            except Exception:  # noqa: BLE001 - unknown or ambiguous id
                bad.append(t)
        if bad:
            raise ValueError(f"unknown docs/THEORY.md section id(s) {bad}; take the ids from list_theorems")
        return self


def report_args_models() -> tuple[type[BaseModel], type[BaseModel]]:
    """``(ReportStep, SubmitReportArgs)``, the pair ``agent._report_args_models`` returns."""
    return ReportStep, SubmitReportArgs


def validate_report(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate report fields (e.g. a human's edit at HITL 2) and return the
    JSON dump. Raises ``pydantic.ValidationError``."""
    return SubmitReportArgs.model_validate(fields).model_dump(mode="json")


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(str(x) for x in e.get('loc', ())) or '(root)'}: {e.get('msg')}"
                 for e in exc.errors(include_url=False)]
        return "; ".join(parts)
    return str(exc)


# ---------------------------------------------------------------------------
# Evidence ids against the ledger
# ---------------------------------------------------------------------------

#: valid ids listed in an unknown-id error at most (the newest are kept)
MAX_LISTED_IDS = 60


def unknown_evidence_ids(report: Mapping[str, Any], evidence: Any) -> dict[str, list[str]]:
    """``{id: [where cited, ...]}`` of the cited ids that have no record in
    ``evidence`` (an ``EvidenceLedger``, its records or a mapping; see
    ``evidence.policy.as_records``). Empty: every id resolves."""
    from ..evidence.policy import as_records

    known = as_records(evidence)
    out: dict[str, list[str]] = {}
    for i, where in cited_evidence_ids(report):
        if i not in known:
            out.setdefault(i, [])
            if where not in out[i]:
                out[i].append(where)
    return out


def _valid_ids_by_tool(evidence: Any) -> tuple[dict[str, list[str]], int]:
    """``({tool: ["<id> (<trust>[, <ir fingerprint>][, by <role>])", ...]}, ids left out)``
    for the successful tool results of the ledger (what a claim can cite), newest last."""
    from ..agent import ledger_evidence

    entries = [e for e in ledger_evidence(evidence) if e.get("ok") and e.get("tool") != SUBMIT_REPORT]
    dropped = max(0, len(entries) - MAX_LISTED_IDS)
    grouped: dict[str, list[str]] = {}
    for e in entries[dropped:]:
        bits = [str(e.get("trust") or "not_verified")]
        if e.get("ir_fingerprint"):
            bits.append(str(e["ir_fingerprint"]))
        if e.get("role") and e.get("role") != "main":
            bits.append(f"by {e['role']}")
        grouped.setdefault(str(e.get("tool")), []).append(f"{e['id']} ({', '.join(bits)})")
    return grouped, dropped


def evidence_id_error(unknown: Mapping[str, Sequence[str]], evidence: Any) -> dict[str, Any]:
    """The error result for a report citing ``unknown`` ids: the unknown ids
    (with where they are cited, and a hint when one is a tool name) and the
    valid ids grouped by tool name, e.g. ``regex_to_dfa -> toolu_01Xb... (verified,
    ir-86ca...)``. Code never maps a name to an id: the model resubmits."""
    grouped, dropped = _valid_ids_by_tool(evidence)
    parts = []
    for i, where in unknown.items():
        hint = " -- a tool NAME, not a call id" if i in grouped else ""
        parts.append(f"'{i}' (in {', '.join(where)}){hint}")
    lines = [f"{tool} -> " + "; ".join(ids) for tool, ids in grouped.items()]
    if dropped:
        lines.append(f"(+{dropped} older result(s) not listed)")
    valid = "\n".join(lines) if lines else "(no successful tool result is recorded yet: run the checks first)"
    text = ("report NOT accepted (nothing was recorded): every evidence id must be the exact tool_call id of a "
            "tool call in the evidence ledger, not a tool name or a label of your own. Unknown id(s): "
            + "; ".join(parts) + ".\nValid ids by tool (trust, IR fingerprint):\n" + valid
            + "\nResubmit submit_report with exact ids from this list (copy them as they are); a claim no tool "
              "result supports gets no id.")
    return {"tool": SUBMIT_REPORT, "ok": False, "error_kind": "unknown_evidence_ids",
            "unknown_ids": list(unknown), "valid_ids": grouped, "error": text}


_TOOL_MARKUP_RE = re.compile(
    r"</?(?:antml:)?(?:parameter|invoke|function_calls)\b"
    r"|</(?:hypothesis|method|construction|summary|conclusion|verdict|claims|steps|lemmas)>")


def leaked_markup_fields(report: Any, path: str = "") -> list[str]:
    """The report fields holding tool-call markup: a malformed call can put
    ``</hypothesis><parameter name="construction">...`` inside one field, which
    swallows the next field and would print the markup in the report."""
    if isinstance(report, str):
        return [path or "report"] if _TOOL_MARKUP_RE.search(report) else []
    if isinstance(report, Mapping):
        return [f for k, v in report.items() for f in leaked_markup_fields(v, f"{path}.{k}" if path else str(k))]
    if isinstance(report, (list, tuple)):
        return [f for i, v in enumerate(report) for f in leaked_markup_fields(v, f"{path}[{i}]")]
    return []


def make_submit_report_tool(evidence: Any = None) -> Any:
    """The ``submit_report`` LangChain tool.

    It validates the structured fields and returns them as the ToolMessage
    artifact ``{"tool": "submit_report", "ok": true, "report": {...}}``; the
    agent's report gate then ends the run (or HITL 2 interrupts first). An
    invalid report returns a JSON error as content, with no artifact, so the
    model gets another turn.

    ``evidence``: the run's ``EvidenceLedger`` (``None``: the ledger of an
    enclosing ``use_evidence`` block at call time, if any). With a ledger, a
    report citing an id that is not in it is refused the same way
    (:func:`evidence_id_error`); without one, ids are not checked here."""
    from langchain_core.tools import StructuredTool, ToolException

    def _ledger() -> Any:
        if evidence is not None:
            return evidence
        from ..evidence.ledger import current_evidence

        return current_evidence()

    def _run(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        report = validate_report(kwargs)
        leaked = leaked_markup_fields(report)
        if leaked:
            raise ToolException(
                f"tool-call markup inside field(s) {', '.join(leaked)}: the call was malformed and one field "
                "swallowed the next. Resubmit with each field holding only its own text.")
        ledger = _ledger()
        if ledger is not None:
            unknown = unknown_evidence_ids(report, ledger)
            if unknown:
                raise ToolException(json.dumps(evidence_id_error(unknown, ledger), ensure_ascii=False))
        content = json.dumps({
            "tool": SUBMIT_REPORT, "ok": True, "status": "report_submitted",
            "template": report["template"],
            "note": "report recorded; the run ends here. Evidence and trust are computed by code.",
        }, ensure_ascii=False)
        return content, {"tool": SUBMIT_REPORT, "ok": True, "report": report}

    def _on_error(exc: Exception) -> str:
        return json.dumps({"tool": SUBMIT_REPORT, "ok": False, "error_kind": "invalid_input",
                           "error": f"report not accepted, fix and resubmit: {_error_text(exc)[:1500]}"},
                          ensure_ascii=False)

    def _on_tool_error(exc: ToolException) -> str:
        return str(exc)

    _run.__name__ = SUBMIT_REPORT
    return StructuredTool.from_function(
        func=_run, name=SUBMIT_REPORT, args_schema=SubmitReportArgs, infer_schema=False,
        response_format="content_and_artifact", handle_validation_error=_on_error,
        handle_tool_error=_on_tool_error,
        description=(
            "Submit the final report and END the run. Give the verdict, the class, the method, the "
            "proof as steps (title, claim, justification, theorem_ids, evidence_ids), optionally the "
            "hypothesis, the lemmas used (docs/THEORY.md section ids), the key claims, the chosen "
            "construction, the conclusion and the Lean block. For every claim and step list the ids "
            "of the tool calls whose results support it. Do not claim that something is verified: "
            "trust labels, the confidence ceiling and the evidence block are generated by code from "
            "the evidence ledger. An evidence id is the tool_call id of the call (e.g. toolu_...), "
            "never the tool's name: a report citing an id that is not in the ledger is refused with "
            "the list of valid ids. "
            "The report-templates skill describes the templates. Call it once, alone, as your last "
            "action."),
        metadata={"tfl_group": "report"},
    )


# ---------------------------------------------------------------------------
# list_report_templates (registry tool)
# ---------------------------------------------------------------------------

class ListReportTemplatesArgs(ToolArgs):
    name: TemplateName | None = Field(default=None, description=(
        "one template to show with its Markdown text and placeholders; omit to list all four"))


@tfl_tool(group="report", max_trust="not_verified", timeout_s=10, allow_refuted=False)
def list_report_templates(args: ListReportTemplatesArgs) -> ToolResult:
    """List the report templates (full_proof, exam_solution, short_verdict, lean_appendix): what each
    is for and which submit_report fields it prints. With name, also return that template's Markdown
    text and placeholders. The report is rendered by code from your submit_report fields; the
    appendix "Проверка" and every trust label are added by code to every
    template. Formatting only: trust is always not_verified."""
    names = [args.name] if args.name else list(SELECTABLE_TEMPLATES)
    templates = []
    for name in names:
        info = TEMPLATE_INFO[name]
        entry: dict[str, Any] = {"name": name, "title": info["title"], "when": info["when"],
                                 "fields": list(info["fields"]),
                                 "file": f"/skills/report-templates/{name}.md"}
        if args.name:
            entry["text"] = load_template(name).template
            entry["placeholders"] = sorted(template_placeholders(name))
            if name == "lean_appendix":
                entry["body_template"] = "full_proof"
        templates.append(entry)
    data = {"templates": templates, "default": "full_proof",
            "skill": "/skills/report-templates/SKILL.md",
            "templates_dir": TEMPLATES_DIR.name,
            "note": ("Pass the name as submit_report.template. The Lean section is appended to any "
                     "template when the report has a lean block or the run has a lean_check result. "
                     "Trust labels and the evidence block are generated by code.")}
    return ToolResult.success(data, trust="not_verified",
                              reason="report templates are formatting, not evidence about the language")
