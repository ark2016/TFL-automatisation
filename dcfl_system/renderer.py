"""
DCFL Renderer — format DCFL pipeline results as JSON, Markdown, and HTML.

Produces human-readable output for deterministic context-free language analysis.
Handles five proof sketch kinds: stack_strategy, closure_reduction,
dcfl_pumping, shallit, inh_ambiguity.

Public API:
    render_json(result)      -> str
    render_markdown(result)  -> str
    render_html(result)      -> str
    render_to_file(result, path, fmt)  -> None
    render_all(result, output_dir, task_id) -> dict[str, str]
"""

from __future__ import annotations

import html as html_module
import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(text: Any) -> str:
    """Return str(text) safe for embedding in HTML."""
    return html_module.escape(str(text))


_VERDICT_LABELS: dict[str, str] = {
    "dcfl": "ДКСЯ (детерминированный)",
    "non_dcfl": "не ДКСЯ (недетерминированный)",
    "inconclusive": "неизвестно",
    "failure": "ошибка пайплайна",
}

_VERDICT_EMOJI: dict[str, str] = {
    "dcfl": "\u2705",       # ✅
    "non_dcfl": "\u274c",   # ❌
    "inconclusive": "\u2753",  # ❓
    "failure": "\u26a0\ufe0f",  # ⚠️
}

_VERDICT_CSS: dict[str, tuple[str, str]] = {
    "dcfl":         ("s-dcfl",    "ДКСЯ"),
    "non_dcfl":     ("s-nondcfl", "НЕ ДКСЯ"),
    "inconclusive": ("s-unknown", "неизвестно"),
    "failure":      ("s-fail-v",  "ошибка"),
}


def _verdict_label(verdict: str | None) -> str:
    return _VERDICT_LABELS.get(verdict or "", "неизвестно")


def _verdict_emoji(verdict: str | None) -> str:
    return _VERDICT_EMOJI.get(verdict or "", "\u2753")


def _safe_get(d: dict | None, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dicts."""
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
    return current


# ---------------------------------------------------------------------------
# Proof sketch rendering (Markdown)
# ---------------------------------------------------------------------------

def _render_proof_sketch_md(sketch: dict | None, method: str | None) -> str:
    """Render a structured proof_sketch as Markdown sections."""
    if sketch is None:
        return ""

    if not isinstance(sketch, dict):
        return ""

    method = method or ""
    parts: list[str] = []

    if method == "stack_strategy" or "phases" in sketch:
        phases = sketch.get("phases") or []
        if phases:
            parts.append("#### Фазы стековой стратегии\n")
            parts.append("| # | Фаза | Действие | Что | Триггер |")
            parts.append("|---|------|----------|-----|---------|")
            for i, phase in enumerate(phases, 1):
                if isinstance(phase, dict):
                    name = phase.get("name", "")
                    action = phase.get("action", phase.get("description", ""))
                    what = phase.get("what", "")
                    trigger = phase.get("trigger", "")
                    parts.append(f"| {i} | {name} | {action} | {what} | {trigger} |")
                elif isinstance(phase, str):
                    parts.append(f"| {i} | — | {phase} | | |")
            parts.append("")
        sep = sketch.get("separator")
        if sep:
            parts.append(f"**Разделитель:** {sep}\n")
        det_arg = sketch.get("determinism_argument")
        if det_arg:
            parts.append(f"**Аргумент детерминированности:** {det_arg}\n")
        finite_ctrl = sketch.get("finite_control")
        if finite_ctrl:
            parts.append(f"**Конечное управление:** {finite_ctrl}\n")
        regex_states = sketch.get("regex_in_states")
        if isinstance(regex_states, list) and regex_states:
            parts.append("**Regex → состояния ДКА:**\n")
            for rs in regex_states:
                parts.append(f"- {rs}")
            parts.append("")

    elif method == "closure_reduction" or "operation" in sketch:
        fields = [
            ("operation", "Операция"),
            ("direction", "Направление"),
            ("source_language", "Исходный язык"),
            ("transformation", "Преобразование"),
            ("result_argument", "Аргумент результата"),
        ]
        for key, label in fields:
            val = sketch.get(key)
            if val:
                parts.append(f"**{label}:** {val}\n")

    elif method == "dcfl_pumping" or "word_w" in sketch or "words" in sketch:
        fields = [
            ("word_w", "Слово w"),
            ("word_w_prime", "Слово w'"),
            ("common_prefix_x", "Общий префикс x"),
            ("suffix_y", "Суффикс y"),
            ("suffix_z", "Суффикс z"),
            ("first_letters_match", "Первые буквы совпадают"),
            ("no_pumping_argument", "Аргумент отсутствия накачки"),
            ("words", "Слова"),
            ("prefix", "Префикс"),
            ("pumping_argument", "Аргумент накачки"),
        ]
        for key, label in fields:
            val = sketch.get(key)
            if val is not None and val != "":
                parts.append(f"**{label}:** {val}\n")
        suffixes = sketch.get("suffixes")
        if suffixes:
            if isinstance(suffixes, list):
                parts.append(f"**Суффиксы:** {', '.join(str(s) for s in suffixes)}\n")
            else:
                parts.append(f"**Суффиксы:** {suffixes}\n")

    elif method == "shallit" or "infinite_set" in sketch or "infinite_set_description" in sketch:
        fields = [
            ("infinite_set_description", "Бесконечное множество"),
            ("infinite_set", "Бесконечное множество"),
            ("separating_context", "Разделяющий контекст"),
            ("two_elements", "Два элемента"),
            ("argument", "Аргумент"),
        ]
        for key, label in fields:
            val = sketch.get(key)
            if val:
                parts.append(f"**{label}:** {val}\n")

    elif method == "inh_ambiguity" or "disjunction" in sketch or "disjunction_identified" in sketch:
        fields = [
            ("disjunction_identified", "Дизъюнкция"),
            ("disjunction", "Дизъюнкция"),
            ("overlap_words", "Пересекающиеся слова"),
            ("ambiguity_argument", "Аргумент неоднозначности"),
            ("dcfl_implication", "Импликация для ДКСЯ"),
        ]
        for key, label in fields:
            val = sketch.get(key)
            if val:
                parts.append(f"**{label}:** {val}\n")

    else:
        # Generic fallback: render all keys
        for key, val in sketch.items():
            if val is not None and val != "":
                label = key.replace("_", " ").capitalize()
                parts.append(f"**{label}:** {val}\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Proof sketch rendering (HTML)
# ---------------------------------------------------------------------------

def _render_proof_sketch_html(sketch: dict | None, method: str | None) -> str:
    """Render a structured proof_sketch as HTML."""
    if sketch is None:
        return ""
    if not isinstance(sketch, dict):
        return ""

    method = method or ""
    parts: list[str] = []

    if method == "stack_strategy" or "phases" in sketch:
        phases = sketch.get("phases") or []
        if phases:
            parts.append('<div class="s-p"><strong>Фазы стековой стратегии:</strong></div>')
            parts.append('<table><thead><tr><th>#</th><th>Фаза</th><th>Действие</th><th>Что</th><th>Триггер</th></tr></thead><tbody>')
            for i, phase in enumerate(phases, 1):
                if isinstance(phase, dict):
                    name = _esc(phase.get("name", ""))
                    action = _esc(phase.get("action", phase.get("description", "")))
                    what = _esc(phase.get("what", ""))
                    trigger = _esc(phase.get("trigger", ""))
                    parts.append(f"<tr><td>{i}</td><td>{name}</td><td>{action}</td><td>{what}</td><td>{trigger}</td></tr>")
                elif isinstance(phase, str):
                    parts.append(f"<tr><td>{i}</td><td>—</td><td>{_esc(phase)}</td><td></td><td></td></tr>")
            parts.append("</tbody></table>")
        sep = sketch.get("separator")
        if sep:
            parts.append(f'<div class="s-p"><strong>Разделитель:</strong> {_esc(sep)}</div>')
        det_arg = sketch.get("determinism_argument")
        if det_arg:
            parts.append(f'<div class="s-box"><strong>Аргумент детерминированности:</strong> {_esc(det_arg)}</div>')
        finite_ctrl = sketch.get("finite_control")
        if finite_ctrl:
            parts.append(f'<div class="s-p"><strong>Конечное управление:</strong> {_esc(finite_ctrl)}</div>')
        regex_states = sketch.get("regex_in_states")
        if isinstance(regex_states, list) and regex_states:
            parts.append('<div class="s-p"><strong>Regex → состояния ДКА:</strong></div><ul>')
            for rs in regex_states:
                parts.append(f'<li>{_esc(rs)}</li>')
            parts.append('</ul>')

    elif method == "closure_reduction" or "operation" in sketch:
        fields = [
            ("operation", "Операция"),
            ("direction", "Направление"),
            ("source_language", "Исходный язык"),
            ("transformation", "Преобразование"),
            ("result_argument", "Аргумент результата"),
        ]
        for key, label in fields:
            val = sketch.get(key)
            if val:
                parts.append(f'<div class="s-p"><strong>{label}:</strong> {_esc(val)}</div>')

    elif method == "dcfl_pumping" or "word_w" in sketch or "words" in sketch:
        fields = [
            ("word_w", "Слово w"),
            ("word_w_prime", "Слово w'"),
            ("common_prefix_x", "Общий префикс x"),
            ("suffix_y", "Суффикс y"),
            ("suffix_z", "Суффикс z"),
            ("first_letters_match", "Первые буквы совпадают"),
            ("no_pumping_argument", "Аргумент отсутствия накачки"),
            # Legacy field names
            ("words", "Слова"),
            ("prefix", "Префикс"),
            ("pumping_argument", "Аргумент накачки"),
        ]
        for key, label in fields:
            val = sketch.get(key)
            if val is not None and val != "":
                parts.append(f'<div class="s-p"><strong>{label}:</strong> {_esc(val)}</div>')
        suffixes = sketch.get("suffixes")
        if suffixes:
            if isinstance(suffixes, list):
                suf_str = ", ".join(_esc(str(s)) for s in suffixes)
            else:
                suf_str = _esc(suffixes)
            parts.append(f'<div class="s-p"><strong>Суффиксы:</strong> {suf_str}</div>')

    elif method == "shallit" or "infinite_set" in sketch or "infinite_set_description" in sketch:
        fields = [
            ("infinite_set_description", "Бесконечное множество"),
            ("infinite_set", "Бесконечное множество"),
            ("separating_context", "Разделяющий контекст"),
            ("two_elements", "Два элемента"),
            ("argument", "Аргумент"),
        ]
        for key, label in fields:
            val = sketch.get(key)
            if val:
                parts.append(f'<div class="s-p"><strong>{label}:</strong> {_esc(val)}</div>')

    elif method == "inh_ambiguity" or "disjunction" in sketch or "disjunction_identified" in sketch:
        fields = [
            ("disjunction_identified", "Дизъюнкция"),
            ("disjunction", "Дизъюнкция"),
            ("overlap_words", "Пересекающиеся слова"),
            ("ambiguity_argument", "Аргумент неоднозначности"),
            ("dcfl_implication", "Импликация для ДКСЯ"),
        ]
        for key, label in fields:
            val = sketch.get(key)
            if val:
                parts.append(f'<div class="s-p"><strong>{label}:</strong> {_esc(val)}</div>')

    else:
        # Generic fallback
        for key, val in sketch.items():
            if val is not None and val != "":
                label = _esc(key.replace("_", " ").capitalize())
                if isinstance(val, (dict, list)):
                    val_str = json.dumps(val, ensure_ascii=False, indent=2)
                    parts.append(
                        f'<div class="s-p"><strong>{label}:</strong></div>'
                        f'<pre class="s-mono" style="background:#f8f9fa;padding:8px;'
                        f'border-radius:4px;white-space:pre-wrap">{_esc(val_str)}</pre>'
                    )
                else:
                    parts.append(f'<div class="s-p"><strong>{label}:</strong> {_esc(val)}</div>')

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON renderer
# ---------------------------------------------------------------------------

def render_json(result: dict) -> str:
    """Render full result as JSON string."""
    if result is None:
        return json.dumps({"error": "no result"}, ensure_ascii=False, indent=2)
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def render_markdown(result: dict) -> str:
    """Convert DCFL pipeline result to Markdown."""
    if result is None:
        return "> [!error] Результат отсутствует\n> Нет данных для отображения.\n"

    sections: list[str] = []

    # Title
    source = result.get("source_text", "")
    sections.append("# Анализ детерминированности КС-языка\n")
    if source:
        sections.append(f"**Задача:** {source}\n")

    # Verdict
    verdict = result.get("verdict")
    conf = result.get("confidence")
    sections.append(
        f"> [!theorem] Вердикт: {_verdict_label(verdict)} {_verdict_emoji(verdict)}"
    )
    if conf is not None:
        try:
            pct = int(float(conf) * 100)
            sections.append(f"> Уверенность: {pct}%\n")
        except (TypeError, ValueError):
            sections.append("")
    else:
        sections.append("")

    # Proof section
    sections.append("## Доказательство\n")

    proof_method = result.get("proof_method")
    proof_sketch = result.get("proof_sketch")
    proof_text = result.get("proof_text")

    if proof_method:
        sections.append(f"**Метод:** {proof_method}\n")

    if proof_sketch is not None:
        sketch_md = _render_proof_sketch_md(proof_sketch, proof_method)
        if sketch_md:
            sections.append(sketch_md)
        elif proof_text:
            sections.append(f"{proof_text}\n")
    elif proof_text:
        sections.append(f"{proof_text}\n")
    else:
        sections.append("*Доказательство не предоставлено.*\n")

    # Reasoning summary
    reasoning_summary = result.get("reasoning_summary")
    if reasoning_summary:
        sections.append(f"**Итог рассуждения:** {reasoning_summary}\n")

    primary_evidence = result.get("primary_evidence")
    if primary_evidence:
        sections.append(f"**Основной источник:** {primary_evidence}\n")

    # Agent results table
    agent_results = result.get("agent_results") or result.get("specialist_outputs")
    if isinstance(agent_results, dict) and agent_results:
        sections.append("### Результаты агентов\n")
        sections.append("| Агент | Статус | Вердикт | Уверенность |")
        sections.append("|-------|--------|---------|-------------|")
        for agent_name, agent_out in agent_results.items():
            if not isinstance(agent_out, dict):
                continue
            status = agent_out.get("status", "unknown")
            a_verdict = agent_out.get("verdict", "—")
            a_conf = agent_out.get("confidence")
            if a_conf is not None:
                try:
                    a_conf_str = f"{float(a_conf):.2f}"
                except (TypeError, ValueError):
                    a_conf_str = str(a_conf)
            else:
                a_conf_str = "—"
            sections.append(f"| {agent_name} | {status} | {a_verdict} | {a_conf_str} |")
        sections.append("")

    # Oracle verification (per-agent dict)
    oracle = result.get("oracle_verification")
    if isinstance(oracle, dict) and oracle:
        sections.append("### Oracle верификация\n")
        sections.append("| Агент | Статус | Проверки |")
        sections.append("|-------|--------|----------|")
        for oname, overify in oracle.items():
            if not isinstance(overify, dict):
                continue
            ostatus = overify.get("verification_status", "—")
            cp = overify.get("checks_passed", 0)
            ct = overify.get("checks_total", 0)
            checks_str = f"{cp}/{ct}" if ct > 0 else "—"
            sections.append(f"| {oname} | {ostatus} | {checks_str} |")
        sections.append("")

    # Errors
    errors = result.get("errors") or []
    if errors:
        sections.append(f"### Ошибки ({len(errors)})\n")
        for e in errors:
            sections.append(f"- {e}")
        sections.append("")

    # Metadata
    metadata = result.get("metadata")
    if isinstance(metadata, dict) and metadata:
        timing = metadata.get("total_time_s")
        retries = metadata.get("retry_count")
        if timing is not None:
            sections.append(f"*Время выполнения: {timing:.2f}s*")
        if retries:
            sections.append(f"*Повторных попыток: {retries}*")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# HTML renderer — per-specialist panel builders
# ---------------------------------------------------------------------------


def _panel_status_line(output: dict) -> str:
    """Return a small status line describing this specialist's result."""
    status = output.get("status", "unknown")
    verdict = output.get("verdict")
    conf = output.get("confidence")
    bits: list[str] = []
    if verdict:
        bits.append(f"вердикт: <code>{_esc(verdict)}</code>")
    bits.append(f"статус: <code>{_esc(status)}</code>")
    if conf is not None:
        try:
            bits.append(f"уверенность: {float(conf):.2f}")
        except (TypeError, ValueError):
            pass
    return f'<div class="s-meta">{" · ".join(bits)}</div>'


def _render_evidence_steps(output: dict, parts: list[str]) -> None:
    """Append numbered evidence steps (s-step/s-n/s-t) if evidence is a list."""
    evidence = output.get("evidence")
    if isinstance(evidence, list) and evidence:
        for i, step in enumerate(evidence, 1):
            parts.append(
                f'<div class="s-step"><div class="s-n">{i}</div>'
                f'<div class="s-t">{_esc(step)}</div></div>'
            )


def _render_errors(output: dict, parts: list[str]) -> None:
    """Append error block if the agent reported errors."""
    agent_errors = output.get("errors") or []
    if agent_errors:
        parts.append('<div class="s-p s-err"><strong>Ошибки:</strong></div><ul>')
        for err in agent_errors:
            parts.append(f"<li>{_esc(err)}</li>")
        parts.append("</ul>")


def _render_fields(sketch: dict | None, fields: list[tuple[str, str]],
                   parts: list[str]) -> None:
    """Render a list of (key, label) fields from a proof_sketch dict."""
    if sketch is None:
        return
    for key, label in fields:
        val = sketch.get(key)
        if val is not None and val != "":
            parts.append(
                f'<div class="s-p"><strong>{label}:</strong> {_esc(val)}</div>'
            )


def _panel_stack_strategy(output: dict) -> str:
    parts = [_panel_status_line(output)]
    sketch = output.get("proof_sketch")
    if isinstance(sketch, dict) and sketch:
        phases = sketch.get("phases") or []
        if phases:
            parts.append(
                '<table><thead><tr><th>#</th><th>Фаза</th><th>Действие</th>'
                '<th>Что</th><th>Триггер</th></tr></thead><tbody>'
            )
            for i, phase in enumerate(phases, 1):
                if isinstance(phase, dict):
                    name = _esc(phase.get("name", ""))
                    action = _esc(
                        phase.get("action", phase.get("description", ""))
                    )
                    what = _esc(phase.get("what", ""))
                    trigger = _esc(phase.get("trigger", ""))
                    parts.append(
                        f"<tr><td>{i}</td><td>{name}</td><td>{action}</td>"
                        f"<td>{what}</td><td>{trigger}</td></tr>"
                    )
                elif isinstance(phase, str):
                    parts.append(
                        f"<tr><td>{i}</td><td>\u2014</td><td>{_esc(phase)}</td>"
                        f"<td></td><td></td></tr>"
                    )
            parts.append("</tbody></table>")

        _render_fields(sketch, [
            ("separator", "Разделитель"),
            ("determinism_argument", "Аргумент детерминированности"),
            ("finite_control", "Конечное управление"),
        ], parts)

        regex_states = sketch.get("regex_in_states")
        if isinstance(regex_states, list) and regex_states:
            parts.append(
                '<div class="s-p"><strong>Regex \u2192 состояния ДКА:</strong></div><ul>'
            )
            for rs in regex_states:
                parts.append(f'<li>{_esc(rs)}</li>')
            parts.append('</ul>')

    _render_evidence_steps(output, parts)
    _render_errors(output, parts)
    return "\n".join(parts)


def _panel_closure_reduction(output: dict) -> str:
    parts = [_panel_status_line(output)]
    sketch = output.get("proof_sketch")
    if isinstance(sketch, dict) and sketch:
        _render_fields(sketch, [
            ("operation", "Операция"),
            ("direction", "Направление"),
            ("source_language", "Исходный язык"),
            ("transformation", "Преобразование"),
            ("result_argument", "Аргумент результата"),
        ], parts)
    _render_evidence_steps(output, parts)
    _render_errors(output, parts)
    return "\n".join(parts)


def _panel_dcfl_pumping(output: dict) -> str:
    parts = [_panel_status_line(output)]
    sketch = output.get("proof_sketch")
    if isinstance(sketch, dict) and sketch:
        _render_fields(sketch, [
            ("word_w", "Слово w"),
            ("word_w_prime", "Слово w\u2032"),
            ("common_prefix_x", "Общий префикс x"),
            ("suffix_y", "Суффикс y"),
            ("suffix_z", "Суффикс z"),
            ("first_letters_match", "Первые буквы совпадают"),
            ("no_pumping_argument", "Аргумент отсутствия накачки"),
        ], parts)
    _render_evidence_steps(output, parts)
    _render_errors(output, parts)
    return "\n".join(parts)


def _panel_shallit(output: dict) -> str:
    parts = [_panel_status_line(output)]
    sketch = output.get("proof_sketch")
    if isinstance(sketch, dict) and sketch:
        _render_fields(sketch, [
            ("infinite_set_description", "Бесконечное множество"),
            ("separating_context", "Разделяющий контекст"),
            ("two_elements", "Два элемента"),
            ("argument", "Аргумент"),
        ], parts)
    _render_evidence_steps(output, parts)
    _render_errors(output, parts)
    return "\n".join(parts)


def _panel_inh_ambiguity(output: dict) -> str:
    parts = [_panel_status_line(output)]
    sketch = output.get("proof_sketch")
    if isinstance(sketch, dict) and sketch:
        _render_fields(sketch, [
            ("disjunction_identified", "Дизъюнкция"),
            ("overlap_words", "Пересекающиеся слова"),
            ("ambiguity_argument", "Аргумент неоднозначности"),
            ("dcfl_implication", "Импликация для ДКСЯ"),
        ], parts)
    _render_evidence_steps(output, parts)
    _render_errors(output, parts)
    return "\n".join(parts)


# Mapping: specialist key -> (tab label, panel builder)
_DCFL_SPECIALIST_PANELS: list[tuple[str, str, Any]] = [
    ("stack_strategy",    "Стековая стратегия",      _panel_stack_strategy),
    ("closure_reduction", "Замыкание",               _panel_closure_reduction),
    ("dcfl_pumping",      "Лемма накачки ДКСЛ",      _panel_dcfl_pumping),
    ("shallit",           "Лемма Шаллита",           _panel_shallit),
    ("inh_ambiguity",     "Существ. неоднозначность", _panel_inh_ambiguity),
]

# Reverse lookup: specialist key -> panel builder (for main proof block)
_PANEL_BUILDER_BY_KEY: dict[str, Any] = {
    name: builder for name, _, builder in _DCFL_SPECIALIST_PANELS
}


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def render_html(result: dict) -> str:
    """Convert DCFL pipeline result to standalone HTML page.

    Structure (matches CFL renderer layout):
      - Header: task text + verdict badge (ДКСЯ / НЕ ДКСЯ)
      - Verification banner: oracle verification summary
      - Main proof block (s-main-proof): winning agent's proof
      - Specialist tabs: one per agent that produced data
      - Hints from reasoning agent
      - Footer: confidence, agent count, verification status dot
    """
    if result is None:
        return "<html><body><p>Результат отсутствует</p></body></html>"

    verdict = result.get("verdict")
    source = _esc(result.get("source_text", ""))
    conf = result.get("confidence")
    proof_method = result.get("proof_method")
    proof_sketch = result.get("proof_sketch")  # MAY BE None!
    proof_text = result.get("proof_text")
    specialist_outputs = (
        result.get("specialist_outputs")
        or result.get("agent_results")
        or {}
    )
    oracle = result.get("oracle_verification")
    metadata = result.get("metadata") or {}
    reasoning_summary = result.get("reasoning_summary")
    agents_used = result.get("agents_used") or []
    errors = result.get("errors") or []

    badge_class, badge_label = _VERDICT_CSS.get(
        verdict or "", ("s-unknown", "неизвестно")
    )

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append("<html lang='ru'><head><meta charset='utf-8'>")
    parts.append("<title>DCFL — Результат анализа</title>")
    parts.append("<style>")
    # CSS — identical structure to CFL renderer, with DCFL class names
    parts.append("body{font-family:'Segoe UI',system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#1a1a1a;background:#fff}")
    parts.append(".s-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #e0e0e0}")
    parts.append(".s-task{font-size:15px;line-height:1.6}")
    parts.append(".s-label-sm{font-size:12px;color:#888;margin-bottom:6px}")
    parts.append(".s-badge{font-size:12px;font-weight:600;padding:4px 14px;border-radius:6px;white-space:nowrap;flex-shrink:0}")
    parts.append(".s-dcfl{background:#e8f5e9;color:#2e7d32}")
    parts.append(".s-nondcfl{background:#fce4ec;color:#c62828}")
    parts.append(".s-unknown{background:#fff3e0;color:#e65100}")
    parts.append(".s-fail-v{background:#ffebee;color:#b71c1c}")
    parts.append(".s-sec{font-size:16px;font-weight:600;margin:24px 0 10px;padding-top:16px;border-top:1px solid #eee}")
    parts.append(".s-sec:first-of-type{border-top:none;padding-top:0}")
    parts.append(".s-p{font-size:14px;line-height:1.75;margin:6px 0}")
    parts.append(".s-meta{font-size:12px;color:#666;margin:4px 0 10px}")
    parts.append(".s-step{display:flex;gap:10px;margin:10px 0}")
    parts.append(".s-n{min-width:22px;height:22px;border-radius:50%;background:#f5f5f5;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#666;flex-shrink:0;margin-top:2px}")
    parts.append(".s-t{font-size:14px;line-height:1.7}")
    parts.append(".s-box{background:#f8f9fa;border-radius:6px;padding:12px 16px;margin:10px 0;font-size:13px;line-height:1.7}")
    parts.append(".s-mono{font-family:Consolas,'Courier New',monospace;font-size:13px}")
    parts.append(".s-tab{display:flex;gap:2px;margin:16px 0 12px;flex-wrap:wrap}")
    parts.append(".s-tab-btn{font-size:12px;padding:6px 14px;border-radius:6px 6px 0 0;background:#f5f5f5;color:#666;cursor:pointer;border:none;border-bottom:2px solid transparent;font-family:inherit}")
    parts.append(".s-tab-btn:hover{background:#eee}")
    parts.append(".s-tab-btn.active{background:#fff;color:#1a1a1a;border-bottom-color:#1976d2}")
    parts.append(".s-panel{display:none}.s-panel.active{display:block}")
    parts.append(".s-footer{display:flex;gap:16px;align-items:center;margin-top:20px;padding-top:14px;border-top:1px solid #eee;font-size:12px;color:#888;flex-wrap:wrap}")
    parts.append(".s-dot{width:7px;height:7px;border-radius:50%;display:inline-block}")
    parts.append(".s-pass{background:#4caf50}.s-fail{background:#e53935}")
    parts.append(".s-pill{font-size:11px;padding:2px 8px;border-radius:6px;background:#e3f2fd;color:#1565c0}")
    parts.append(".s-banner-ok{background:#e8f5e9;border-left:4px solid #2e7d32;padding:10px 14px;border-radius:4px;margin:12px 0;font-size:13px}")
    parts.append(".s-banner-warn{background:#fff3e0;border-left:4px solid #f57c00;padding:10px 14px;border-radius:4px;margin:12px 0;font-size:13px}")
    parts.append(".s-main-proof{background:#f1f8e9;border-left:4px solid #4caf50;padding:14px 18px;border-radius:4px;margin:14px 0}")
    parts.append(".s-err{color:#c62828}")
    parts.append("table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}")
    parts.append("th,td{border:1px solid #e0e0e0;padding:6px 10px;text-align:left}")
    parts.append("th{background:#f5f5f5;font-weight:600}")
    parts.append("</style></head><body>")

    # --- Header ---
    parts.append('<div class="s-head">')
    parts.append('  <div>')
    parts.append('    <div class="s-label-sm">Задача (анализ детерминированности КС-языка)</div>')
    parts.append(f'    <div class="s-task">{source if source else "\u2014"}</div>')
    parts.append('  </div>')
    parts.append(f'  <span class="s-badge {badge_class}">{_esc(badge_label)}</span>')
    parts.append('</div>')

    # --- Verification banner (oracle, per-agent dict) ---
    any_verified = False
    if isinstance(oracle, dict) and oracle:
        any_verified = any(
            isinstance(v, dict) and v.get("verification_status") == "verified"
            for v in oracle.values()
        )
        verified_count = sum(
            1 for v in oracle.values()
            if isinstance(v, dict) and v.get("verification_status") == "verified"
        )
        total_agents = sum(
            1 for v in oracle.values()
            if isinstance(v, dict) and v.get("verification_status") not in (
                "not_applicable", "not_verified", None
            )
        )
        if any_verified:
            parts.append(
                '<div class="s-banner-ok">'
                f'<strong>\u2713 Oracle верификация пройдена</strong> '
                f'({verified_count} из {total_agents} агентов верифицировано)'
                '</div>'
            )
        else:
            parts.append(
                '<div class="s-banner-warn">'
                '<strong>\u26a0 Oracle верификация НЕ пройдена</strong><br>'
                'Ни один агент не получил статус "verified". '
                'Ниже приведён аргумент специалиста-агента, а не проверенная теорема. '
                'Требуется ручная проверка.'
                '</div>'
            )

    # --- Main proof block ---
    if proof_sketch is not None or proof_text:
        parts.append('<div class="s-sec">Основное доказательство</div>')
        parts.append('<div class="s-main-proof">')
        if proof_method:
            parts.append(
                f'<div class="s-p"><strong>Метод:</strong> {_esc(proof_method)}</div>'
            )
        if proof_sketch is not None:
            # Try the matching panel builder for a richer rendering
            builder = _PANEL_BUILDER_BY_KEY.get(proof_method or "")
            if builder is not None:
                # Build a synthetic output dict so the panel builder works
                synthetic = {
                    "proof_sketch": proof_sketch,
                    "status": "success",
                    "verdict": verdict,
                }
                panel_html = builder(synthetic)
                # Strip the status line from the panel (already shown in header)
                if panel_html.startswith('<div class="s-meta">'):
                    idx = panel_html.find("</div>")
                    if idx != -1:
                        panel_html = panel_html[idx + len("</div>") :].strip()
                if panel_html:
                    parts.append(panel_html)
                else:
                    sketch_html = _render_proof_sketch_html(
                        proof_sketch, proof_method
                    )
                    parts.append(
                        sketch_html
                        or '<div class="s-p" style="color:#888">'
                        'Структура доказательства пуста.</div>'
                    )
            else:
                sketch_html = _render_proof_sketch_html(
                    proof_sketch, proof_method
                )
                parts.append(
                    sketch_html
                    or '<div class="s-p" style="color:#888">'
                    'Структура доказательства пуста.</div>'
                )
        elif proof_text:
            parts.append(
                f'<div style="white-space:pre-wrap">{_esc(proof_text)}</div>'
            )
        parts.append('</div>')

    # Reasoning summary
    if reasoning_summary:
        parts.append(
            '<div class="s-box" style="background:#e3f2fd;'
            'border-left:4px solid #1976d2;margin:12px 0">'
        )
        parts.append('<div class="s-p"><strong>Итог рассуждения:</strong></div>')
        parts.append(
            f'<div style="white-space:pre-wrap">{_esc(reasoning_summary)}</div>'
        )
        parts.append('</div>')

    # --- Specialist tabs ---
    proof_tabs: list[tuple[str, str]] = []
    if isinstance(specialist_outputs, dict) and specialist_outputs:
        for name, label, builder in _DCFL_SPECIALIST_PANELS:
            out = specialist_outputs.get(name)
            if not isinstance(out, dict):
                continue
            try:
                panel_html = builder(out)
            except Exception as exc:
                panel_html = (
                    f'<div class="s-p" style="color:#c62828">'
                    f'Ошибка рендера: {_esc(exc)}</div>'
                )
            proof_tabs.append((label, panel_html))

    if proof_tabs:
        parts.append('<div class="s-sec">Методы специалистов</div>')
        parts.append('<div class="s-tab">')
        for i, (label, _) in enumerate(proof_tabs):
            active = " active" if i == 0 else ""
            parts.append(
                f'  <button class="s-tab-btn{active}" '
                f'onclick="showTab({i})">{_esc(label)}</button>'
            )
        parts.append('</div>')
        for i, (_, panel_html) in enumerate(proof_tabs):
            active = " active" if i == 0 else ""
            parts.append(f'<div class="s-panel{active}" id="panel-{i}">')
            parts.append(panel_html)
            parts.append('</div>')

    # --- Oracle verification detail table ---
    if isinstance(oracle, dict) and oracle:
        parts.append('<div class="s-sec">Oracle верификация (детали)</div>')
        parts.append(
            '<table><thead><tr><th>Агент</th><th>Статус</th>'
            '<th>Проверки</th><th>Проблемы</th></tr></thead><tbody>'
        )
        for oname, overify in oracle.items():
            if not isinstance(overify, dict):
                continue
            ostatus = _esc(overify.get("verification_status", "\u2014"))
            cp = overify.get("checks_passed", 0)
            ct = overify.get("checks_total", 0)
            checks_str = f"{cp}/{ct}" if ct > 0 else "\u2014"
            issues = overify.get("issues", [])
            issues_str = (
                _esc("; ".join(str(i) for i in issues)) if issues else "\u2014"
            )
            parts.append(
                f"<tr><td>{_esc(oname)}</td><td><code>{ostatus}</code></td>"
                f"<td>{checks_str}</td><td>{issues_str}</td></tr>"
            )
        parts.append('</tbody></table>')

    # --- Hints ---
    hints = result.get("hints_for_human") or []
    if hints:
        parts.append('<div class="s-sec">Подсказки и наблюдения</div>')
        for hint in hints:
            parts.append(
                f'<div class="s-step"><div class="s-n">\U0001f4a1</div>'
                f'<div class="s-t">{_esc(hint)}</div></div>'
            )

    # --- Pipeline errors ---
    if errors:
        parts.append(
            f'<div class="s-sec">Ошибки пайплайна ({len(errors)})</div><ul>'
        )
        for e in errors:
            parts.append(f'<li style="color:#c62828">\u26a0 {_esc(e)}</li>')
        parts.append('</ul>')

    # --- Footer ---
    footer_items: list[str] = []
    if conf is not None:
        try:
            footer_items.append(f'<span>confidence: {float(conf):.2f}</span>')
        except (TypeError, ValueError):
            pass
    if agents_used:
        footer_items.append(
            f'<span class="s-pill">{len(agents_used)} агентов отработали</span>'
        )
    elif isinstance(specialist_outputs, dict) and specialist_outputs:
        footer_items.append(
            f'<span class="s-pill">{len(specialist_outputs)} agents</span>'
        )
    if any_verified:
        footer_items.append(
            '<span style="display:flex;align-items:center;gap:5px">'
            '<span class="s-dot s-pass"></span>verified</span>'
        )
    elif isinstance(oracle, dict) and oracle:
        footer_items.append(
            '<span style="display:flex;align-items:center;gap:5px">'
            '<span class="s-dot s-fail"></span>not verified</span>'
        )

    if footer_items:
        parts.append('<div class="s-footer">')
        for item in footer_items:
            parts.append(f'  {item}')
        parts.append('</div>')

    # Tab-switching JS
    parts.append('<script>')
    parts.append('function showTab(n){')
    parts.append("  document.querySelectorAll('.s-tab-btn').forEach((b,i)=>b.classList.toggle('active',i===n));")
    parts.append("  document.querySelectorAll('.s-panel').forEach((p,i)=>p.classList.toggle('active',i===n));")
    parts.append("  if(window.renderMathInElement){try{renderMathInElement(document.querySelector('.s-panel.active'),{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\\\[',right:'\\\\]',display:true},{left:'\\\\(',right:'\\\\)',display:false}],throwOnError:false});}catch(e){}}")
    parts.append('}')
    parts.append('</script>')

    # KaTeX CDN (always include — DCFL proofs commonly use LaTeX)
    parts.append(
        '<link rel="stylesheet" '
        'href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css" '
        'integrity="sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+" '
        'crossorigin="anonymous">'
    )
    parts.append(
        '<script defer '
        'src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js" '
        'integrity="sha384-7zkQWkzuo3B5mTepMUcHkMB5jZaolc2xDwL6VFqjFALcbeS9Ggm/Yr2r3Dy4lfFg" '
        'crossorigin="anonymous"></script>'
    )
    parts.append(
        '<script defer '
        'src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" '
        'integrity="sha384-43gviWU0YVjaDtb/GhzOouOXtZMP/7XUzwPTstBeZFe/+rCMvRwr4yROQP43s0Xk" '
        'crossorigin="anonymous" '
        'onload="renderMathInElement(document.body,{delimiters:['
        "{left:'$$',right:'$$',display:true},"
        "{left:'$',right:'$',display:false},"
        "{left:'\\\\[',right:'\\\\]',display:true},"
        "{left:'\\\\(',right:'\\\\)',display:false}"
        '],throwOnError:false});"></script>'
    )

    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# File output
# ---------------------------------------------------------------------------

def render_to_file(result: dict, path: str, fmt: str = "json") -> None:
    """Write rendered output to file. fmt: 'json', 'md', 'html'."""
    renderers = {
        "json": render_json,
        "md": render_markdown,
        "html": render_html,
    }
    renderer = renderers.get(fmt)
    if renderer is None:
        raise ValueError(f"Unknown format {fmt!r}; expected one of {list(renderers)}")
    content = renderer(result)
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


def render_all(result: dict, output_dir: str, task_id: str) -> dict[str, str]:
    """Render all formats to output_dir. Returns {fmt: filepath}."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths: dict[str, str] = {}
    for fmt, ext in [("json", ".json"), ("md", ".md"), ("html", ".html")]:
        file_path = out / f"{task_id}{ext}"
        render_to_file(result, str(file_path), fmt)
        paths[fmt] = str(file_path)
    return paths
