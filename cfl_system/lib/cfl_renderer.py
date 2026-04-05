"""
CFL Renderer — format pipeline results as Obsidian-compatible Markdown and HTML.

Produces human-readable output with callouts, LaTeX, and tables
suitable for displaying to students or embedding in reports.
"""

from __future__ import annotations

import html as html_module
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

_re_sub = re.sub

# Cache graphviz availability across calls — checked once per process.
_DOT_SENTINEL = object()
_DOT_BINARY: Any = _DOT_SENTINEL

def _find_dot_binary() -> str | None:
    """Locate the Graphviz `dot` binary, once per process."""
    global _DOT_BINARY
    if _DOT_BINARY is not _DOT_SENTINEL:
        return _DOT_BINARY
    _DOT_BINARY = shutil.which("dot")
    return _DOT_BINARY


def _render_dot_to_svg(dot_src: str) -> str | None:
    """Render DOT source to inline SVG via the `dot` binary.

    Returns the SVG as a string (with the XML declaration stripped so it
    embeds cleanly inside HTML), or None if graphviz is unavailable or
    the render fails.
    """
    dot_bin = _find_dot_binary()
    if not dot_bin:
        return None
    try:
        result = subprocess.run(
            [dot_bin, "-Tsvg"],
            input=dot_src,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    svg = result.stdout
    # Strip XML declaration and DOCTYPE — browsers render inline SVG fine
    # without them, and they can cause validation warnings.
    svg = re.sub(r'<\?xml[^?]*\?>\s*', '', svg)
    svg = re.sub(r'<!DOCTYPE[^>]*>\s*', '', svg)
    return svg.strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MD_TABLE_RE = re.compile(
    # Matches a markdown table block:
    #   header row:    | col1 | col2 | ... |
    #   separator:     |------|------| ... |
    #   body rows:     | val  | val  | ... |  (one or more)
    r'(^\|[^\n]*\|[ \t]*\n'        # header
    r'\|[\s:|\-]+\|[ \t]*\n'       # separator (dashes, colons, pipes, spaces)
    r'(?:\|[^\n]*\|[ \t]*\n?)+)',  # body rows
    re.MULTILINE,
)


def _render_md_inline(text: str) -> str:
    """Convert a chunk of markdown-ish text to HTML.

    Handles:
      - Markdown tables (| col | col | / |---|---| / | val | val |) → <table>
      - HTML entity escaping for non-table text (< > & " ')
      - Preserves `$...$` and `\\(...\\)` LaTeX for KaTeX auto-render
      - Single newlines → <br>, blank lines → paragraph breaks

    Used for formalizer step content where the LLM may produce
    markdown tables mixed with LaTeX formulas.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)

    pieces: list[str] = []
    last_end = 0
    for match in _MD_TABLE_RE.finditer(text):
        # Escape and render the prose before this table
        prose_before = text[last_end:match.start()]
        if prose_before:
            pieces.append(_render_prose_block(prose_before))
        # Render the table block
        pieces.append(_md_table_to_html(match.group(1)))
        last_end = match.end()
    # Trailing prose after the last table
    if last_end < len(text):
        pieces.append(_render_prose_block(text[last_end:]))
    return "".join(pieces)


def _render_prose_block(text: str) -> str:
    """Escape HTML chars, preserve $ for KaTeX, convert newlines."""
    # Escape <, >, &, ", ' but NOT $ (KaTeX delimiter)
    escaped = (text
               .replace("&", "&amp;")
               .replace("<", "&lt;")
               .replace(">", "&gt;")
               .replace('"', "&quot;")
               .replace("'", "&#39;"))
    # Double newline → paragraph break; single newline → <br>
    escaped = escaped.replace("\n\n", "</p><p>").replace("\n", "<br>")
    if escaped.strip():
        return f"<p>{escaped}</p>"
    return ""


def _md_table_to_html(md: str) -> str:
    """Convert a single markdown table block to an HTML <table>."""
    lines = [l for l in md.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return f"<pre>{_esc(md)}</pre>"

    def _split_row(row: str) -> list[str]:
        # Strip leading/trailing pipes, split on pipes, strip each cell
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        return [cell.strip() for cell in row.split("|")]

    header = _split_row(lines[0])
    # lines[1] is the separator — skip it
    body = [_split_row(l) for l in lines[2:]]

    out = ['<table class="s-md-table"><thead><tr>']
    for h in header:
        # Escape HTML but preserve $ for KaTeX
        out.append(f"<th>{_render_prose_block(h).replace('<p>', '').replace('</p>', '')}</th>")
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>")
        # Pad missing cells
        while len(row) < len(header):
            row.append("")
        for cell in row:
            cell_html = _render_prose_block(cell).replace("<p>", "").replace("</p>", "")
            out.append(f"<td>{cell_html}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


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
    if not isinstance(grammar, dict):
        return ""
    rules = grammar.get("rules") or []
    if not rules:
        return ""
    by_lhs: dict[str, list[str]] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        lhs = rule.get("lhs")
        rhs = rule.get("rhs", [])
        if not lhs:
            continue
        rhs_str = " ".join(str(s) for s in rhs) if rhs else "ε"
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
    if not isinstance(pda, dict):
        return ""
    transitions = pda.get("transitions") or []
    if not transitions:
        return ""

    header = "| From | Input | Stack Top | To | Push |"
    sep = "|------|-------|-----------|-----|------|"
    rows = [header, sep]
    for t in transitions:
        if not isinstance(t, dict):
            continue
        inp = t.get("input") or "ε"
        push_list = t.get("push") or []
        push = " ".join(str(s) for s in push_list) if push_list else "ε (pop)"
        rows.append(
            f"| {t.get('from', '?')} | {inp} | {t.get('stack_top', '?')} "
            f"| {t.get('to', '?')} | {push} |"
        )
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

    # Verification banner — critical for honesty about whether proof_checker ran
    if "proof_verified" in result:
        if result.get("proof_verified"):
            sections.append("> [!success] Доказательство прошло независимую проверку верификатором\n")
        else:
            sections.append(
                "> [!warning] Доказательство НЕ прошло независимую проверку\n"
                "> Верификатор не запустился или не подтвердил корректность. "
                "Приведённое ниже доказательство — это аргумент специалиста-агента, "
                "а не проверенная теорема. Требуется ручная проверка.\n"
            )

    # Proof
    proof = result.get("proof")
    if proof is not None:
        if isinstance(proof, str):
            # Raw markdown proof from formalizer
            sections.append("> [!proof] Доказательство")
            for line in proof.split("\n"):
                sections.append(f"> {line}")
            sections.append("")
        elif isinstance(proof, dict):
            # Two possible schemas:
            # 1) Fallback (formalizer agent failed): {source, note, evidence, summary}
            #    where `evidence` is the raw specialist output
            #    (e.g. parikh.evidence with explanation/conclusion fields).
            # 2) Formalizer proof_document: {title, method, summary, steps, conclusion, references}
            is_fallback = "evidence" in proof and "source" in proof

            if is_fallback:
                src = proof.get("source", "")
                sections.append(f"> [!proof] Доказательство (источник: {src})")
                note = proof.get("note")
                if note:
                    sections.append(f"> *{note}*")
                sections.append("")
                summary = proof.get("summary")
                if summary:
                    sections.append("### Краткое изложение\n")
                    sections.append(f"{summary}\n")
                ev = proof.get("evidence") or {}
                if isinstance(ev, dict):
                    explanation = ev.get("explanation")
                    if explanation:
                        sections.append("### Подробное обоснование\n")
                        sections.append(f"{explanation}\n")
                    # Parikh-specific fields
                    img = ev.get("commutative_image")
                    if img:
                        sections.append(f"**Образ Париха:** {img}\n")
                    semi = ev.get("is_semilinear")
                    if semi is not None:
                        sections.append(f"**Полулинейность:** {'да' if semi else 'нет'}\n")
                    # Pumping-specific fields
                    cases = ev.get("cases")
                    if cases:
                        sections.append("### Разбор случаев\n")
                        sections.append(_render_pumping_cases(cases))
                        sections.append("")
                    word = ev.get("word_chosen") or ev.get("word_parametric")
                    if word:
                        sections.append(f"> [!note] Выбранное слово\n> $z = {word}$\n")
                    concl = ev.get("conclusion")
                    if concl:
                        sections.append(f"### Заключение\n\n{concl}\n")
            else:
                # Formalizer proof_document schema
                title = proof.get("title", "")
                method = proof.get("method", "")
                summary = proof.get("summary", "")

                header = f"> [!proof] {title}" if title else "> [!proof] Доказательство"
                if method:
                    header += f" ({method})"
                sections.append(header)
                if summary:
                    sections.append(f"> {summary}\n")
                else:
                    sections.append("")

                # Formalizer structured steps
                steps = proof.get("steps") or []
                if steps:
                    sections.append("### Шаги доказательства\n")
                    for step in steps:
                        if not isinstance(step, dict):
                            continue
                        num = step.get("step_number", "")
                        s_title = step.get("title", "")
                        content = step.get("content", "")
                        justification = step.get("justification", "")
                        sections.append(f"**Шаг {num}. {s_title}**\n")
                        if content:
                            sections.append(f"{content}\n")
                        if justification:
                            sections.append(f"*Обоснование:* {justification}\n")

                # Conclusion (formalizer)
                conclusion = proof.get("conclusion")
                if conclusion:
                    sections.append(f"### Заключение\n\n{conclusion}\n")

                # References (formalizer)
                refs = proof.get("references") or []
                if refs:
                    sections.append(f"**Источники:** {', '.join(refs)}\n")

                # Legacy fields: details with pumping cases / word_chosen
                details = proof.get("details") or {}
                cases = details.get("cases")
                if cases:
                    sections.append("### Разбор случаев (накачка)\n")
                    sections.append(_render_pumping_cases(cases))
                    sections.append("")
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
    if isinstance(oracle, dict) and oracle.get("status") != "not_applicable":
        sections.append("### Oracle тест\n")
        sections.append(f"- Статус: {oracle.get('status', 'N/A')}")
        sections.append(f"- Проверено положительных: {oracle.get('positive_checked', 0)}")
        sections.append(f"- Проверено отрицательных: {oracle.get('negative_checked', 0)}")
        ces = oracle.get("counterexamples") or []
        if ces:
            sections.append(f"- Контрпримеры: {len(ces)}")
            for ce in ces[:5]:
                if isinstance(ce, dict):
                    word = ce.get("word", "")
                    desc = ce.get("description", "")
                    sections.append(f"  - `{word}`: {desc}")
                else:
                    sections.append(f"  - `{ce}`")
        sections.append("")

    # Agents used
    agents = result.get("agents_used", [])
    if agents:
        sections.append(f"**Агенты (успешно):** {', '.join(agents)}")

    # Agents that were dispatched but failed — important for coverage transparency
    failed = result.get("agents_failed") or []
    if failed:
        sections.append(f"\n**Агенты (с ошибками):** {len(failed)}")
        for f in failed:
            if isinstance(f, dict):
                name = f.get("agent", "?")
                err = f.get("error", "")
                sections.append(f"- `{name}` — {err}")

    retries = result.get("retries", 0)
    if retries:
        sections.append(f"\n**Повторных попыток:** {retries}")

    # Pipeline-level errors
    errs = result.get("errors") or []
    if errs:
        sections.append(f"\n### Ошибки пайплайна ({len(errs)})\n")
        for e in errs:
            sections.append(f"- {e}")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-specialist HTML panel builders
#
# Each builder receives the raw agent output dict and returns inner HTML for
# one tab panel. Always return non-empty HTML so the tab is worth rendering.
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


def _panel_pumping(output: dict) -> str:
    """Panel for pumping_cfl or ogden specialists."""
    ev = output.get("evidence") or {}
    parts = [_panel_status_line(output)]
    if not isinstance(ev, dict) or not ev:
        errs = output.get("errors") or []
        if errs:
            parts.append(
                f'<div class="s-p" style="color:#888">Не удалось построить доказательство. '
                f'Ошибки: {_esc("; ".join(str(e) for e in errs))}</div>'
            )
        else:
            parts.append('<div class="s-p" style="color:#888">Специалист не смог построить доказательство накачкой.</div>')
        return "\n".join(parts)

    word = ev.get("word_chosen") or ev.get("word_parametric")
    if word:
        parts.append(f'<div class="s-p">Выбранное слово: <span class="s-mono">z = {_esc(word)}</span></div>')
    mem = ev.get("membership_argument")
    if mem:
        parts.append(f'<div class="s-p"><strong>Принадлежность z ∈ L:</strong> {_esc(mem)}</div>')
    length = ev.get("length_argument")
    if length:
        parts.append(f'<div class="s-p"><strong>Длина:</strong> {_esc(length)}</div>')

    cases = ev.get("cases") or []
    if cases and isinstance(cases, list):
        parts.append('<div class="s-p"><strong>Разбор случаев:</strong></div>')
        for i, c in enumerate(cases, 1):
            if not isinstance(c, dict):
                continue
            desc = c.get("case", "")
            region = c.get("vwx_region", "")
            pumped = c.get("pumped_word", "")
            why = c.get("why_not_in_L", "")
            pump_val = c.get("pump_value", "")
            head = _esc(desc)
            if region:
                head += f" <em>({_esc(region)})</em>"
            body = ""
            if pumped:
                body += f'Накачанное слово: <span class="s-mono">{_esc(pumped)}</span>'
                if pump_val != "":
                    body += f" (i = {_esc(pump_val)})"
                body += "<br>"
            if why:
                body += _esc(why)
            parts.append(
                f'<div class="s-step"><div class="s-n">{i}</div>'
                f'<div class="s-t"><strong>{head}</strong><br>{body}</div></div>'
            )

    all_cov = ev.get("all_cases_covered")
    if all_cov is False:
        parts.append('<div class="s-p" style="color:#e65100">⚠ Не все случаи покрыты.</div>')

    concl = ev.get("conclusion")
    if concl:
        parts.append(f'<div class="s-box"><strong>Заключение.</strong> {_esc(concl)}</div>')
    return "\n".join(parts)


def _panel_parikh(output: dict) -> str:
    ev = output.get("evidence") or {}
    parts = [_panel_status_line(output)]
    if not isinstance(ev, dict) or not ev:
        parts.append('<div class="s-p" style="color:#888">Нет данных от агента Париха.</div>')
        return "\n".join(parts)

    img = ev.get("commutative_image")
    if img:
        parts.append(f'<div class="s-p"><strong>Образ Париха Ψ(L):</strong> <span class="s-mono">{_esc(img)}</span></div>')
    semi = ev.get("is_semilinear")
    if semi is not None:
        label = "да — значит необходимое условие КС выполнено" if semi else "нет — следовательно L НЕ КС (по контрапозиции теоремы Париха)"
        parts.append(f'<div class="s-p"><strong>Полулинейность:</strong> {label}</div>')
    semi_repr = ev.get("semilinear_representation")
    if semi_repr:
        parts.append(f'<div class="s-p"><strong>Представление:</strong> <span class="s-mono">{_esc(semi_repr)}</span></div>')
    expl = ev.get("explanation")
    if expl:
        parts.append(f'<div class="s-box"><strong>Обоснование.</strong><br><span style="white-space:pre-wrap">{_esc(expl)}</span></div>')
    concl = ev.get("conclusion")
    if concl:
        parts.append(f'<div class="s-box"><strong>Заключение.</strong> {_esc(concl)}</div>')
    return "\n".join(parts)


def _panel_grammar(output: dict) -> str:
    ev = output.get("evidence") or {}
    parts = [_panel_status_line(output)]
    grammar = None
    if isinstance(ev, dict):
        grammar = ev.get("grammar")
    if grammar is None:
        grammar = output.get("grammar")
    if isinstance(grammar, dict):
        rules = grammar.get("rules") or []
        if rules:
            by_lhs: dict[str, list[str]] = {}
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                lhs = rule.get("lhs")
                rhs = rule.get("rhs", [])
                if not lhs:
                    continue
                rhs_str = " ".join(str(s) for s in rhs) if rhs else "ε"
                by_lhs.setdefault(lhs, []).append(_esc(rhs_str))
            parts.append('<div class="s-box"><strong>КС-грамматика G:</strong><ul style="margin:6px 0">')
            start = grammar.get("start")
            if start:
                parts.append(f'<li>Стартовый нетерминал: <span class="s-mono">{_esc(start)}</span></li>')
            for nt, alts in by_lhs.items():
                parts.append(f'<li><span class="s-mono">{_esc(nt)} → {" | ".join(alts)}</span></li>')
            parts.append('</ul></div>')
    else:
        parts.append('<div class="s-p" style="color:#888">Грамматика не построена.</div>')

    if isinstance(ev, dict):
        expl = ev.get("explanation") or ev.get("construction_idea")
        if expl:
            parts.append(f'<div class="s-p">{_esc(expl)}</div>')
    return "\n".join(parts)


def _pda_to_dot(pda: dict) -> str:
    """Generate Graphviz DOT source for a PDA.

    Labels follow standard PDA notation: `input, stack_top / push`.
    ε represents epsilon. Parallel transitions between the same pair of
    states are collapsed into a single multi-line label for readability.
    Start state gets an arrow from a hidden node; accept states are
    drawn with double circles.
    """
    transitions = pda.get("transitions") or []
    start = pda.get("start") or pda.get("initial_state") or "q0"
    accepts = pda.get("accept_states") or pda.get("final_states") or pda.get("accepting_states") or []
    if not isinstance(accepts, list):
        accepts = [accepts]
    accept_set = {str(a) for a in accepts}

    # Collect all states referenced in transitions + start + accepts
    states: list[str] = []
    seen: set[str] = set()
    def _add_state(s: str) -> None:
        if s not in seen:
            seen.add(s)
            states.append(s)
    _add_state(str(start))
    for t in transitions:
        if isinstance(t, dict):
            _add_state(str(t.get("from", "?")))
            _add_state(str(t.get("to", "?")))
    for a in accept_set:
        _add_state(a)

    def _dot_id(s: str) -> str:
        # Quote the identifier to allow any characters
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'

    def _dot_label(s: str) -> str:
        return s.replace('\\', '\\\\').replace('"', '\\"')

    lines = [
        "digraph PDA {",
        '  rankdir=LR;',
        '  node [fontname="Segoe UI", fontsize=11, shape=circle];',
        '  edge [fontname="Consolas", fontsize=10];',
        '  bgcolor="transparent";',
        '  __hidden_start [shape=point, width=0, height=0, label=""];',
    ]

    # State declarations (accept states are doublecircle)
    for s in states:
        if s in accept_set:
            lines.append(f'  {_dot_id(s)} [shape=doublecircle];')
        else:
            lines.append(f'  {_dot_id(s)};')

    # Start arrow
    lines.append(f'  __hidden_start -> {_dot_id(str(start))};')

    # Collapse parallel transitions (same src→dst) into one labeled edge
    edges: dict[tuple[str, str], list[str]] = {}
    for t in transitions:
        if not isinstance(t, dict):
            continue
        src = str(t.get("from", "?"))
        dst = str(t.get("to", "?"))
        inp = t.get("input") or "ε"
        if inp == "":
            inp = "ε"
        stack_top = t.get("stack_top", "?")
        push_list = t.get("push") or []
        push_str = "".join(str(s) for s in push_list) if push_list else "ε"
        label = f"{inp},{stack_top}/{push_str}"
        edges.setdefault((src, dst), []).append(label)

    for (src, dst), labels in edges.items():
        # Graphviz accepts \n in quoted labels as line breaks
        joined = "\\n".join(_dot_label(lbl) for lbl in labels)
        lines.append(f'  {_dot_id(src)} -> {_dot_id(dst)} [label="{joined}"];')

    lines.append("}")
    return "\n".join(lines)


def _pda_to_mermaid(pda: dict) -> str:
    """Generate a mermaid stateDiagram-v2 source for a PDA.

    Labels have the form 'input, stack_top / push' — the standard PDA
    transition notation. Epsilon is rendered as 'ε'. Stack 'pop' (empty
    push) is rendered as 'ε'.
    """
    transitions = pda.get("transitions") or []
    start = pda.get("start") or pda.get("initial_state") or "q0"
    accepts = pda.get("accept_states") or pda.get("final_states") or pda.get("accepting_states") or []
    if not isinstance(accepts, list):
        accepts = [accepts]

    def _clean_id(s: Any) -> str:
        # Mermaid state IDs: letters, digits, underscore, hyphen are safe.
        return _re_sub(r'[^A-Za-z0-9_]', '_', str(s))

    def _clean_label(s: Any) -> str:
        # Labels cannot contain unescaped double-quotes or newlines.
        return str(s).replace('"', "'").replace("\n", " ")

    lines = ["stateDiagram-v2", f"  [*] --> {_clean_id(start)}"]

    # Collapse multiple transitions between the same pair into one multi-line label
    edges: dict[tuple[str, str], list[str]] = {}
    for t in transitions:
        if not isinstance(t, dict):
            continue
        src = _clean_id(t.get("from", "?"))
        dst = _clean_id(t.get("to", "?"))
        inp = t.get("input") or "ε"
        if inp == "":
            inp = "ε"
        stack_top = t.get("stack_top", "?")
        push_list = t.get("push") or []
        if push_list:
            push_str = "".join(str(s) for s in push_list)
        else:
            push_str = "ε"
        label = _clean_label(f"{inp},{stack_top}/{push_str}")
        edges.setdefault((src, dst), []).append(label)

    for (src, dst), labels in edges.items():
        # Mermaid escapes newlines in labels as <br> only inside quoted labels.
        joined = " | ".join(labels) if len(labels) <= 3 else f"{labels[0]} | {labels[1]} | +{len(labels)-2} ещё"
        lines.append(f"  {src} --> {dst}: {joined}")

    for acc in accepts:
        lines.append(f"  {_clean_id(acc)} --> [*]")

    return "\n".join(lines)


def _render_pda_html_table(pda: dict) -> str:
    """Render PDA transitions as a proper HTML table (not markdown)."""
    transitions = pda.get("transitions") or []
    if not transitions:
        return ""
    parts = ['<table class="s-pda-table">',
             '<tr><th>From</th><th>Input</th><th>Stack Top</th><th>To</th><th>Push</th></tr>']
    for t in transitions:
        if not isinstance(t, dict):
            continue
        inp = _esc(t.get("input") or "ε")
        push_list = t.get("push") or []
        push = _esc(" ".join(str(s) for s in push_list) if push_list else "ε (pop)")
        parts.append(
            f'<tr><td><span class="s-mono">{_esc(t.get("from", "?"))}</span></td>'
            f'<td><span class="s-mono">{inp}</span></td>'
            f'<td><span class="s-mono">{_esc(t.get("stack_top", "?"))}</span></td>'
            f'<td><span class="s-mono">{_esc(t.get("to", "?"))}</span></td>'
            f'<td><span class="s-mono">{push}</span></td></tr>'
        )
    parts.append('</table>')
    return "\n".join(parts)


def _panel_pda(output: dict) -> str:
    ev = output.get("evidence") or {}
    parts = [_panel_status_line(output)]
    pda = None
    if isinstance(ev, dict):
        pda = ev.get("pda")
    if pda is None:
        pda = output.get("pda")
    if isinstance(pda, dict):
        # Try Graphviz first → inline SVG (best quality, fully standalone).
        # Fall back to mermaid if `dot` binary is unavailable.
        diagram_html = None
        dot_src = _pda_to_dot(pda)
        if dot_src:
            svg = _render_dot_to_svg(dot_src)
            if svg:
                diagram_html = f'<div class="s-diagram s-diagram-svg">{svg}</div>'
        if diagram_html is None:
            mermaid_src = _pda_to_mermaid(pda)
            if mermaid_src:
                diagram_html = f'<div class="mermaid">\n{mermaid_src}\n</div>'

        if diagram_html:
            parts.append('<div class="s-p"><strong>Диаграмма состояний:</strong></div>')
            parts.append(diagram_html)

        table_html = _render_pda_html_table(pda)
        if table_html:
            parts.append('<div class="s-p" style="margin-top:12px"><strong>Таблица переходов:</strong></div>')
            parts.append(table_html)
    else:
        parts.append('<div class="s-p" style="color:#888">МП-автомат не построен.</div>')
    if isinstance(ev, dict):
        expl = ev.get("explanation") or ev.get("construction_idea")
        if expl:
            parts.append(f'<div class="s-p" style="margin-top:12px">{_esc(expl)}</div>')
    return "\n".join(parts)


def _panel_generic(output: dict, main_fields: list[str]) -> str:
    """Generic panel builder: status line + known evidence fields + explanation/conclusion."""
    ev = output.get("evidence") or {}
    parts = [_panel_status_line(output)]
    if not isinstance(ev, dict) or not ev:
        errs = output.get("errors") or []
        if errs:
            parts.append(
                f'<div class="s-p" style="color:#888">Нет результата. Ошибки: '
                f'{_esc("; ".join(str(e) for e in errs))}</div>'
            )
        else:
            parts.append('<div class="s-p" style="color:#888">Специалист не дал применимого доказательства.</div>')
        return "\n".join(parts)

    for field in main_fields:
        val = ev.get(field)
        if val is None or val == "":
            continue
        label = field.replace("_", " ").capitalize()
        if isinstance(val, (dict, list)):
            import json as _json
            val_str = _json.dumps(val, ensure_ascii=False, indent=2)
            parts.append(
                f'<div class="s-p"><strong>{_esc(label)}:</strong></div>'
                f'<pre class="s-mono" style="background:#f8f9fa;padding:8px;border-radius:4px;white-space:pre-wrap">{_esc(val_str)}</pre>'
            )
        else:
            parts.append(f'<div class="s-p"><strong>{_esc(label)}:</strong> {_esc(str(val))}</div>')

    expl = ev.get("explanation") or ev.get("argument") or ev.get("reasoning")
    if expl:
        parts.append(f'<div class="s-box"><span style="white-space:pre-wrap">{_esc(expl)}</span></div>')

    concl = ev.get("conclusion")
    if concl:
        parts.append(f'<div class="s-box"><strong>Заключение.</strong> {_esc(concl)}</div>')
    return "\n".join(parts)


# Mapping: specialist name → (tab label, panel builder)
_SPECIALIST_PANELS: list[tuple[str, str, Any]] = [
    ("pumping_cfl",       "Лемма Бар-Хиллеля", _panel_pumping),
    ("ogden",             "Лемма Огдена",      _panel_pumping),
    ("parikh",            "Теорема Париха",    _panel_parikh),
    ("cfg_builder",       "КС-грамматика",     _panel_grammar),
    ("pda_builder",       "МП-автомат",        _panel_pda),
    ("decomposition",     "Декомпозиция",      lambda o: _panel_generic(o, ["decomposition", "sublanguages", "operation"])),
    ("closure_reduction", "Замыкание",         lambda o: _panel_generic(o, ["reduction_from", "operations", "target_language"])),
    ("interchange",       "Interchange-лемма", lambda o: _panel_generic(o, ["word_family", "interchange_argument", "contradiction"])),
    ("morphism",          "Гомоморфизм",       lambda o: _panel_generic(o, ["morphism", "target_language", "preimage_argument"])),
]


def render_html(result: dict) -> str:
    """Convert CFL pipeline result to standalone tabbed HTML page.

    Structure (modeled after agent_system/examples/output/task_z1wawbz2.html):
      - Header: task text + verdict badge
      - Verification banner (verified / not verified)
      - Primary proof callout (from formalizer or fallback)
      - Tabs: one per specialist that produced data
      - Hints from reasoning agent
      - Footer with confidence / agent counts / errors
    """
    if result is None:
        return "<html><body><p>Результат отсутствует</p></body></html>"

    verdict = result.get("verdict")
    source = _esc(result.get("source_text", ""))
    conf = result.get("confidence")

    verdict_class_map = {
        "cfl":     ("s-reg",    "КС"),
        "non_cfl": ("s-nonreg", "НЕ КС"),
    }
    badge_class, badge_label = verdict_class_map.get(verdict, ("s-unknown", "неопределён"))

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append("<html lang='ru'><head><meta charset='utf-8'>")
    parts.append("<title>CFL — Результат анализа</title>")
    parts.append("<style>")
    parts.append("body{font-family:'Segoe UI',system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#1a1a1a;background:#fff}")
    parts.append(".s-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #e0e0e0}")
    parts.append(".s-task{font-size:15px;line-height:1.6}")
    parts.append(".s-label-sm{font-size:12px;color:#888;margin-bottom:6px}")
    parts.append(".s-badge{font-size:12px;font-weight:600;padding:4px 14px;border-radius:6px;white-space:nowrap;flex-shrink:0}")
    parts.append(".s-reg,.verdict-cfl{background:#e8f5e9;color:#2e7d32}")
    parts.append(".s-nonreg,.verdict-non-cfl{background:#fce4ec;color:#c62828}")
    parts.append(".s-unknown,.verdict-unknown{background:#fff3e0;color:#e65100}")
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
    parts.append("table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}")
    parts.append("th,td{border:1px solid #e0e0e0;padding:6px 10px;text-align:left}")
    parts.append("th{background:#f5f5f5;font-weight:600}")
    parts.append(".s-pda-table td,.s-pda-table th{padding:4px 8px;font-size:12px}")
    parts.append(".s-md-table{margin:10px 0;font-size:13px}")
    parts.append(".s-md-table th{background:#f5f5f5;font-weight:600;text-align:left}")
    parts.append(".s-md-table td,.s-md-table th{padding:6px 10px;border:1px solid #e0e0e0}")
    parts.append(".s-justification{margin-top:6px;color:#666;font-size:13px}")
    parts.append(".s-step p{margin:6px 0}")
    parts.append(".s-diagram{background:#fafafa;border:1px solid #e8e8e8;border-radius:6px;padding:14px;margin:10px 0;overflow-x:auto;text-align:center}")
    parts.append(".s-diagram svg{max-width:100%;height:auto}")
    parts.append(".mermaid{background:#fafafa;border-radius:6px;padding:12px;margin:10px 0;overflow-x:auto;text-align:center}")
    parts.append("</style></head><body>")

    # --- Header ---
    parts.append('<div class="s-head">')
    parts.append('  <div>')
    parts.append('    <div class="s-label-sm">Задача (анализ КС-свойства)</div>')
    parts.append(f'    <div class="s-task">{source if source else "—"}</div>')
    parts.append('  </div>')
    # Include legacy class alongside new one for backward compat with tests
    legacy_class = {"cfl": "verdict-cfl", "non_cfl": "verdict-non-cfl"}.get(verdict, "verdict-unknown")
    parts.append(f'  <span class="s-badge {badge_class} {legacy_class}">{_esc(badge_label)}</span>')
    parts.append('</div>')

    # --- Verification banner ---
    if "proof_verified" in result:
        if result.get("proof_verified"):
            parts.append(
                '<div class="s-banner-ok">'
                '<strong>✓ Доказательство прошло независимую проверку верификатором</strong>'
                '</div>'
            )
        else:
            parts.append(
                '<div class="s-banner-warn">'
                '<strong>⚠ Доказательство НЕ прошло независимую проверку</strong><br>'
                'Верификатор не запустился или не подтвердил корректность. '
                'Ниже приведён аргумент специалиста-агента, а не проверенная теорема. '
                'Требуется ручная проверка.'
                '</div>'
            )

    # --- Main proof (from formalizer or fallback) ---
    proof = result.get("proof")
    if proof is not None:
        parts.append('<div class="s-sec">Основное доказательство</div>')
        parts.append('<div class="s-main-proof">')
        if isinstance(proof, str):
            parts.append(f'<div style="white-space:pre-wrap">{_esc(proof)}</div>')
        elif isinstance(proof, dict):
            is_fallback = "evidence" in proof and "source" in proof
            if is_fallback:
                src = _esc(proof.get("source", ""))
                parts.append(f'<div class="s-p"><em>Источник: {src} (формализатор не запустился)</em></div>')
                summary = proof.get("summary")
                if summary:
                    parts.append(f'<div class="s-p">{_esc(summary)}</div>')
                ev = proof.get("evidence") or {}
                if isinstance(ev, dict):
                    expl = ev.get("explanation")
                    if expl:
                        parts.append(f'<div class="s-box" style="background:#fff"><span style="white-space:pre-wrap">{_esc(expl)}</span></div>')
                    concl = ev.get("conclusion")
                    if concl:
                        parts.append(f'<div class="s-p"><strong>Заключение:</strong> {_esc(concl)}</div>')
            else:
                # Formalizer proof_document schema
                title = proof.get("title", "")
                if title:
                    parts.append(f'<div class="s-p"><strong>{_esc(title)}</strong></div>')
                summary = proof.get("summary", "")
                if summary:
                    parts.append(f'<div class="s-p">{_esc(summary)}</div>')
                for step in proof.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    num = step.get("step_number", "")
                    s_title = step.get("title", "")
                    content = step.get("content", "")
                    just = step.get("justification", "")
                    parts.append(
                        f'<div class="s-step"><div class="s-n">{_esc(num)}</div>'
                        f'<div class="s-t"><strong>{_esc(s_title)}</strong>'
                        f'{_render_md_inline(content)}'
                    )
                    if just:
                        parts.append(f'<div class="s-justification"><em>{_render_md_inline(just)}</em></div>')
                    parts.append('</div></div>')
                concl = proof.get("conclusion")
                if concl:
                    parts.append(f'<div class="s-p"><strong>Заключение:</strong> {_esc(concl)}</div>')
        parts.append('</div>')

    # --- Specialist tabs ---
    specialist_outputs = result.get("specialist_outputs") or {}
    proof_tabs: list[tuple[str, str]] = []
    if isinstance(specialist_outputs, dict) and specialist_outputs:
        for name, label, builder in _SPECIALIST_PANELS:
            out = specialist_outputs.get(name)
            if not isinstance(out, dict):
                continue
            try:
                panel_html = builder(out)
            except Exception as exc:
                panel_html = f'<div class="s-p" style="color:#c62828">Ошибка рендера: {_esc(exc)}</div>'
            proof_tabs.append((label, panel_html))

    if proof_tabs:
        parts.append('<div class="s-sec">Методы специалистов</div>')
        parts.append('<div class="s-tab">')
        for i, (label, _) in enumerate(proof_tabs):
            active = " active" if i == 0 else ""
            parts.append(f'  <button class="s-tab-btn{active}" onclick="showTab({i})">{_esc(label)}</button>')
        parts.append('</div>')
        for i, (_, panel_html) in enumerate(proof_tabs):
            active = " active" if i == 0 else ""
            parts.append(f'<div class="s-panel{active}" id="panel-{i}">')
            parts.append(panel_html)
            parts.append('</div>')

    # --- Top-level grammar / PDA (backward compat: shown when there are no
    # specialist outputs but the result dict still carries constructive data,
    # e.g. from legacy tests or external consumers).
    has_cfg_tab = "cfg_builder" in (specialist_outputs if isinstance(specialist_outputs, dict) else {})
    has_pda_tab = "pda_builder" in (specialist_outputs if isinstance(specialist_outputs, dict) else {})

    grammar = result.get("grammar")
    if isinstance(grammar, dict) and not has_cfg_tab:
        parts.append('<div class="s-sec">Грамматика</div>')
        rules = grammar.get("rules") or []
        if rules:
            by_lhs: dict[str, list[str]] = {}
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                lhs = rule.get("lhs")
                rhs = rule.get("rhs", [])
                if not lhs:
                    continue
                rhs_str = " ".join(str(s) for s in rhs) if rhs else "ε"
                by_lhs.setdefault(lhs, []).append(_esc(rhs_str))
            parts.append('<table><tr><th>Нетерминал</th><th>Правила</th></tr>')
            for nt, alts in by_lhs.items():
                parts.append(f'<tr><td><span class="s-mono">{_esc(nt)}</span></td>'
                             f'<td><span class="s-mono">{" | ".join(alts)}</span></td></tr>')
            parts.append('</table>')

    pda = result.get("pda")
    if isinstance(pda, dict) and not has_pda_tab:
        parts.append('<div class="s-sec">МП-автомат</div>')
        transitions = pda.get("transitions") or []
        if transitions:
            parts.append('<table><tr><th>From</th><th>Input</th><th>Stack Top</th>'
                         '<th>To</th><th>Push</th></tr>')
            for t in transitions:
                if not isinstance(t, dict):
                    continue
                inp = _esc(t.get("input") or "ε")
                push_list = t.get("push") or []
                push = _esc(" ".join(str(s) for s in push_list) if push_list else "ε")
                parts.append(
                    f"<tr><td>{_esc(t.get('from', '?'))}</td><td>{inp}</td>"
                    f"<td>{_esc(t.get('stack_top', '?'))}</td>"
                    f"<td>{_esc(t.get('to', '?'))}</td><td>{push}</td></tr>"
                )
            parts.append('</table>')

    # --- Oracle test ---
    oracle = result.get("oracle_test")
    if oracle and isinstance(oracle, dict) and oracle.get("status") != "not_applicable":
        parts.append('<div class="s-sec">Oracle-тест</div>')
        parts.append(f'<div class="s-p">Статус: <code>{_esc(oracle.get("status", "N/A"))}</code>, '
                     f'проверено положительных: {_esc(oracle.get("positive_checked", 0))}, '
                     f'отрицательных: {_esc(oracle.get("negative_checked", 0))}</div>')
        ces = oracle.get("counterexamples") or []
        if ces:
            parts.append(f'<div class="s-p"><strong>Контрпримеры ({len(ces)}):</strong></div><ul>')
            for ce in ces[:5]:
                if isinstance(ce, dict):
                    word = _esc(ce.get("word", ""))
                    desc = _esc(ce.get("description", ""))
                    parts.append(f'<li><code>{word}</code>: {desc}</li>')
            parts.append('</ul>')

    # --- Hints ---
    hints = result.get("hints_for_human") or []
    if hints:
        parts.append('<div class="s-sec">Подсказки и наблюдения</div>')
        for hint in hints:
            parts.append(
                f'<div class="s-step"><div class="s-n">💡</div>'
                f'<div class="s-t">{_esc(hint)}</div></div>'
            )

    # --- Failed agents ---
    failed = result.get("agents_failed") or []
    if failed:
        parts.append(f'<div class="s-sec">Агенты с ошибками ({len(failed)})</div><ul>')
        for f in failed:
            if isinstance(f, dict):
                name = _esc(f.get("agent", "?"))
                err = _esc(f.get("error", ""))
                parts.append(f'<li><code>{name}</code> — {err}</li>')
        parts.append('</ul>')

    # --- Pipeline errors ---
    errs = result.get("errors") or []
    if errs:
        parts.append(f'<div class="s-sec">Ошибки пайплайна ({len(errs)})</div><ul>')
        for e in errs:
            parts.append(f'<li style="color:#c62828">⚠ {_esc(e)}</li>')
        parts.append('</ul>')

    # --- Footer ---
    footer_items: list[str] = []
    if conf is not None:
        try:
            footer_items.append(f'<span>confidence: {float(conf):.2f}</span>')
        except (TypeError, ValueError):
            pass
    agents_used = result.get("agents_used") or []
    if agents_used:
        footer_items.append(f'<span class="s-pill">{len(agents_used)} агентов отработали</span>')
    if failed:
        footer_items.append(f'<span class="s-pill" style="background:#ffebee;color:#c62828">{len(failed)} упали</span>')
    if result.get("proof_verified"):
        footer_items.append('<span style="display:flex;align-items:center;gap:5px"><span class="s-dot s-pass"></span>verified</span>')
    elif "proof_verified" in result:
        footer_items.append('<span style="display:flex;align-items:center;gap:5px"><span class="s-dot s-fail"></span>not verified</span>')

    if footer_items:
        parts.append('<div class="s-footer">')
        for item in footer_items:
            parts.append(f'  {item}')
        parts.append('</div>')

    parts.append('<script>')
    parts.append('function showTab(n){')
    parts.append("  document.querySelectorAll('.s-tab-btn').forEach((b,i)=>b.classList.toggle('active',i===n));")
    parts.append("  document.querySelectorAll('.s-panel').forEach((p,i)=>p.classList.toggle('active',i===n));")
    # Mermaid diagrams live inside hidden .s-panel elements. When a panel is
    # shown for the first time, mermaid may not have rendered its SVG (it
    # skips display:none blocks). Re-run mermaid on tab switch to ensure
    # the diagram appears.
    parts.append("  if(window.mermaid){try{window.mermaid.run({querySelector:'.s-panel.active .mermaid'});}catch(e){}}")
    # KaTeX auto-render: formulas inside previously-hidden panels need
    # a second pass once the panel becomes visible.
    parts.append("  if(window.renderMathInElement){try{window.renderMathInElement(document.querySelector('.s-panel.active'),{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\\\[',right:'\\\\]',display:true},{left:'\\\\(',right:'\\\\)',display:false}],throwOnError:false});}catch(e){}}")
    parts.append('}')
    parts.append('</script>')

    # Conditional CDN dependencies — only included when actually needed
    # so the HTML stays standalone for simple cases.
    html_so_far = "\n".join(parts)
    needs_mermaid = 'class="mermaid"' in html_so_far
    # Heuristic: LaTeX is present if $ or \( appears in content. We check
    # before the script blocks above (the script text itself uses $, but
    # html_so_far currently includes that). So use a content-only probe:
    # look for $$, $...$, \(...\), or \[...\] in proof/main content.
    needs_katex = bool(re.search(r'\$\$|\\\(|\\\[', html_so_far) or re.search(r'\$[^$\n]{1,200}\$', html_so_far))

    if needs_katex:
        # KaTeX CSS + JS + auto-render extension. The auto-render script
        # walks the DOM on load and replaces $...$ / $$...$$ with rendered
        # math. Delimiters matched: $$...$$, $...$, \[...\], \(...\).
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

    if needs_mermaid:
        parts.append(
            '<script type="module">'
            "import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';"
            "mermaid.initialize({startOnLoad:true,theme:'neutral',securityLevel:'loose'});"
            "window.mermaid=mermaid;"
            '</script>'
        )

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
