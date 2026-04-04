"""
CFL Renderer — format pipeline results as Obsidian-compatible Markdown and HTML.

Produces human-readable output with callouts, LaTeX, and tables
suitable for displaying to students or embedding in reports.
"""

from __future__ import annotations

import html as html_module
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(text: Any) -> str:
    """Return str(text) safe for embedding in HTML."""
    return html_module.escape(str(text))


def _verdict_label(verdict: str | None) -> str:
    if verdict == "cfl":
        return "КС (context-free)"
    if verdict == "non_cfl":
        return "не КС (not context-free)"
    return "неизвестно (unknown)"


def _verdict_emoji(verdict: str | None) -> str:
    if verdict == "cfl":
        return "✅"
    if verdict == "non_cfl":
        return "❌"
    return "❓"


def _confidence_bar(confidence: float | None) -> str:
    if confidence is None:
        return ""
    pct = int(confidence * 100)
    return f"Уверенность: {pct}%"


# ---------------------------------------------------------------------------
# Grammar rendering
# ---------------------------------------------------------------------------

def render_grammar(grammar: dict | None) -> str:
    """Render a CFG as readable text: S → aSb | ε."""
    if grammar is None:
        return ""
    rules = grammar.get("rules", [])
    if not rules:
        return ""
    by_lhs: dict[str, list[str]] = {}
    for rule in rules:
        lhs = rule["lhs"]
        rhs = rule["rhs"]
        rhs_str = " ".join(rhs) if rhs else "ε"
        by_lhs.setdefault(lhs, []).append(rhs_str)

    lines = []
    start = grammar.get("start", "")
    # Render start symbol first
    if start in by_lhs:
        alts = " | ".join(by_lhs[start])
        lines.append(f"${start} \\to {alts}$")
    for nt, alts_list in by_lhs.items():
        if nt == start:
            continue
        alts = " | ".join(alts_list)
        lines.append(f"${nt} \\to {alts}$")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PDA rendering
# ---------------------------------------------------------------------------

def render_pda_table(pda: dict | None) -> str:
    """Render PDA transitions as a Markdown table."""
    if pda is None:
        return ""
    transitions = pda.get("transitions", [])
    if not transitions:
        return ""

    header = "| From | Input | Stack Top | To | Push |"
    sep = "|------|-------|-----------|-----|------|"
    rows = [header, sep]
    for t in transitions:
        inp = t.get("input") or "ε"
        push_list = t.get("push", [])
        push = " ".join(push_list) if push_list else "ε (pop)"
        rows.append(f"| {t['from']} | {inp} | {t['stack_top']} | {t['to']} | {push} |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Pumping proof rendering
# ---------------------------------------------------------------------------

def _render_pumping_cases(cases: list[dict]) -> str:
    """Render pumping proof cases as a table."""
    if not cases:
        return ""
    header = "| Случай | Накачанное слово | Почему ∉ L |"
    sep = "|--------|------------------|------------|"
    rows = [header, sep]
    for c in cases:
        case_desc = c.get("case", "")
        pumped = c.get("pumped_word", "")
        reason = c.get("why_not_in_L", "")
        rows.append(f"| {case_desc} | ${pumped}$ | {reason} |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def render_markdown(result: dict) -> str:
    """Convert CFL pipeline result to Obsidian-compatible Markdown."""
    if result is None:
        return "> [!error] Результат отсутствует\n> Нет данных для отображения.\n"

    sections: list[str] = []

    # Title
    source = result.get("source_text", "")
    sections.append(f"# Анализ КС-свойства\n")
    if source:
        sections.append(f"**Задача:** {source}\n")

    # Verdict
    verdict = result.get("verdict")
    conf = result.get("confidence")
    sections.append(f"> [!theorem] Вердикт: {_verdict_label(verdict)} {_verdict_emoji(verdict)}")
    if conf is not None:
        sections.append(f"> {_confidence_bar(conf)}\n")
    else:
        sections.append("")

    # Proof
    proof = result.get("proof")
    if proof is not None:
        method = proof.get("method", "")
        summary = proof.get("summary", "")
        sections.append(f"> [!proof] Доказательство ({method})")
        sections.append(f"> {summary}\n")

        details = proof.get("details", {})

        # Pumping cases
        cases = details.get("cases")
        if cases:
            sections.append("### Разбор случаев (накачка)\n")
            sections.append(_render_pumping_cases(cases))
            sections.append("")

        # Word chosen
        word = details.get("word_chosen") or details.get("word_parametric")
        if word:
            sections.append(f"> [!note] Выбранное слово\n> $z = {word}$\n")

    # Grammar
    grammar = result.get("grammar")
    if grammar is not None:
        sections.append("### Грамматика\n")
        sections.append(render_grammar(grammar))
        sections.append("")

    # PDA
    pda = result.get("pda")
    if pda is not None:
        sections.append("### МП-автомат\n")
        sections.append(render_pda_table(pda))
        sections.append("")

    # Oracle test
    oracle = result.get("oracle_test")
    if oracle and oracle.get("status") != "not_applicable":
        sections.append("### Oracle тест\n")
        sections.append(f"- Статус: {oracle.get('status', 'N/A')}")
        sections.append(f"- Проверено положительных: {oracle.get('positive_checked', 0)}")
        sections.append(f"- Проверено отрицательных: {oracle.get('negative_checked', 0)}")
        ces = oracle.get("counterexamples", [])
        if ces:
            sections.append(f"- Контрпримеры: {len(ces)}")
            for ce in ces[:5]:
                sections.append(f"  - `{ce.get('word', '')}`: {ce.get('description', '')}")
        sections.append("")

    # Agents used
    agents = result.get("agents_used", [])
    if agents:
        sections.append(f"**Агенты:** {', '.join(agents)}")

    retries = result.get("retries", 0)
    if retries:
        sections.append(f"**Повторных попыток:** {retries}")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def render_html(result: dict) -> str:
    """Convert CFL pipeline result to standalone HTML page."""
    if result is None:
        return "<html><body><p>Результат отсутств��ет</p></body></html>"

    verdict = result.get("verdict")
    source = _esc(result.get("source_text", ""))
    conf = result.get("confidence")

    verdict_class = {
        "cfl": "verdict-cfl",
        "non_cfl": "verdict-non-cfl",
    }.get(verdict, "verdict-unknown")

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append("<html lang='ru'><head><meta charset='utf-8'>")
    parts.append("<title>Анализ КС-свойства</title>")
    parts.append("<style>")
    parts.append("body{font-family:sans-serif;max-width:800px;margin:2em auto;padding:0 1em}")
    parts.append(".verdict-cfl{color:#2e7d32;font-weight:bold}")
    parts.append(".verdict-non-cfl{color:#c62828;font-weight:bold}")
    parts.append(".verdict-unknown{color:#f57f17;font-weight:bold}")
    parts.append("table{border-collapse:collapse;margin:1em 0}")
    parts.append("th,td{border:1px solid #ccc;padding:0.4em 0.8em;text-align:left}")
    parts.append(".callout{border-left:4px solid #1976d2;padding:0.5em 1em;margin:1em 0;background:#e3f2fd}")
    parts.append(".proof-callout{border-left-color:#4caf50;background:#e8f5e9}")
    parts.append("</style></head><body>")

    parts.append(f"<h1>Анализ КС-свойства</h1>")
    if source:
        parts.append(f"<p><strong>Задача:</strong> {source}</p>")

    parts.append(f'<div class="callout"><strong>Вердикт:</strong> '
                 f'<span class="{verdict_class}">{_esc(_verdict_label(verdict))}</span>')
    if conf is not None:
        parts.append(f"<br>{_esc(_confidence_bar(conf))}")
    parts.append("</div>")

    # Proof
    proof = result.get("proof")
    if proof is not None:
        method = _esc(proof.get("method", ""))
        summary = _esc(proof.get("summary", ""))
        parts.append(f'<div class="callout proof-callout">')
        parts.append(f"<strong>Доказательство ({method}):</strong><br>{summary}")
        parts.append("</div>")

    # Grammar
    grammar = result.get("grammar")
    if grammar is not None:
        parts.append("<h3>Грамматика</h3>")
        rules = grammar.get("rules", [])
        if rules:
            parts.append("<ul>")
            by_lhs: dict[str, list[str]] = {}
            for rule in rules:
                rhs_str = " ".join(rule["rhs"]) if rule["rhs"] else "ε"
                by_lhs.setdefault(rule["lhs"], []).append(_esc(rhs_str))
            for nt, alts in by_lhs.items():
                parts.append(f"<li>{_esc(nt)} → {' | '.join(alts)}</li>")
            parts.append("</ul>")

    # PDA
    pda = result.get("pda")
    if pda is not None:
        parts.append("<h3>МП-автомат</h3>")
        transitions = pda.get("transitions", [])
        if transitions:
            parts.append("<table><tr><th>From</th><th>Input</th><th>Stack Top</th>"
                         "<th>To</th><th>Push</th></tr>")
            for t in transitions:
                inp = _esc(t.get("input") or "ε")
                push_list = t.get("push", [])
                push = _esc(" ".join(push_list) if push_list else "ε")
                parts.append(f"<tr><td>{_esc(t['from'])}</td><td>{inp}</td>"
                             f"<td>{_esc(t['stack_top'])}</td><td>{_esc(t['to'])}</td>"
                             f"<td>{push}</td></tr>")
            parts.append("</table>")

    # Oracle test
    oracle = result.get("oracle_test")
    if oracle and oracle.get("status") != "not_applicable":
        parts.append("<h3>Oracle тест</h3>")
        parts.append(f"<p>Статус: {_esc(oracle.get('status', 'N/A'))}</p>")

    # Agents
    agents = result.get("agents_used", [])
    if agents:
        parts.append(f"<p><strong>Агенты:</strong> {_esc(', '.join(agents))}</p>")

    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# File output
# ---------------------------------------------------------------------------

def render_to_file(result: dict, path: str, fmt: str = "md") -> None:
    """Write result to file in given format ('md' or 'html')."""
    if fmt == "html":
        content = render_html(result)
    else:
        content = render_markdown(result)
    Path(path).write_text(content, encoding="utf-8")
