"""
LL Renderer — format pipeline results as Markdown, HTML, JSON, and Lean 4 stub.

Outputs:
  - result.json  — machine-readable
  - result.md    — human-readable Markdown
  - result.html  — exam-style HTML with KaTeX, tabs
  - result.lean  — Lean 4 proof stub (incomplete, for reference)

Usage:
    from ll_system.renderer import render_result
    outputs = render_result(result, output_dir="ll_system/examples/output/")
"""

from __future__ import annotations

import html as html_module
import json
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _esc(text: Any) -> str:
    """Return str(text) safe for embedding in HTML."""
    return html_module.escape(str(text))


def _render_prose_block(text: str) -> str:
    """Escape HTML chars, preserve $ for KaTeX, convert newlines."""
    escaped = (text
               .replace("&", "&amp;")
               .replace("<", "&lt;")
               .replace(">", "&gt;")
               .replace('"', "&quot;")
               .replace("'", "&#39;"))
    escaped = escaped.replace("\n\n", "</p><p>").replace("\n", "<br>")
    if escaped.strip():
        return f"<p>{escaped}</p>"
    return ""


def _esc_inline(text: str) -> str:
    """Escape HTML, then apply **bold**, *italic*, `code` inline markdown."""
    s = (text
         .replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;"))
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', s)
    s = re.sub(r'`([^`]+)`', lambda m: f'<code>{html_module.escape(m.group(1))}</code>', s)
    return s


def _render_md_lines(text: str) -> str:
    """Line-by-line MD→HTML conversion (no display-math handling here)."""
    if not text:
        return ""
    out: list[str] = []
    in_list = False
    table_rows: list[str] = []

    def _flush_table() -> None:
        if not table_rows:
            return
        html = ['<table style="border-collapse:collapse;margin:8px 0">']
        for ri, row in enumerate(table_rows):
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            tag = "th" if ri == 0 else "td"
            cell_style = "padding:4px 12px;border:1px solid #ccc;"
            if ri == 0:
                cell_style += "background:#f5f5f5;font-weight:bold;"
            html.append(
                "<tr>"
                + "".join(
                    f'<{tag} style="{cell_style}">{_esc_inline(c)}</{tag}>'
                    for c in cells
                )
                + "</tr>"
            )
        html.append("</table>")
        out.append("\n".join(html))
        table_rows.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        is_pipe_row = stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2
        is_separator = is_pipe_row and all(c in "|-: " for c in stripped)

        if is_pipe_row and not is_separator:
            if in_list:
                out.append("</ul>")
                in_list = False
            table_rows.append(line)
            continue
        if is_separator and table_rows:
            continue  # skip alignment row
        if table_rows:
            _flush_table()

        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("### "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f'<h4 style="margin:14px 0 6px">{_esc_inline(stripped[4:])}</h4>')
        elif stripped.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f'<h3 style="margin:16px 0 8px">{_esc_inline(stripped[3:])}</h3>')
        elif stripped.startswith("# "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f'<h3 style="margin:16px 0 8px">{_esc_inline(stripped[2:])}</h3>')
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_esc_inline(stripped[2:])}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{_esc_inline(line)}</p>")

    if table_rows:
        _flush_table()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _render_md_block(text: str) -> str:
    """Convert a simple Markdown document to HTML.

    Handles: ## / ### headers, **bold**, *italic*, `code`, - bullet lists,
    blank lines (paragraph breaks), inline $math$ for KaTeX,
    display math $$...$$ blocks (including multi-line aligned), pipe tables.

    Display-math blocks are emitted verbatim (no HTML escaping) so that
    KaTeX auto-render can find the $$...$$  delimiters and the & alignment
    operators inside \\begin{aligned} are not mangled to &amp;.
    """
    if not text or not isinstance(text, str):
        return ""

    # Line-based detection: a display-math block starts only when a line's
    # stripped content begins with $$.  This avoids false matches on $\$$
    # inside pipe-table cells (where $$ appears mid-line after a leading |).
    lines = text.split("\n")
    out_html: list[str] = []
    plain_lines: list[str] = []

    def _flush_plain() -> None:
        if plain_lines:
            out_html.append(_render_md_lines("\n".join(plain_lines)))
            plain_lines.clear()

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if stripped.startswith("$$"):
            _flush_plain()
            math_lines = [lines[i]]

            if stripped.endswith("$$") and len(stripped) > 4:
                # Complete single-line block: $$...$$
                i += 1
            else:
                # Multi-line block (e.g. $$\begin{aligned}...\end{aligned}$$)
                i += 1
                while i < len(lines):
                    math_lines.append(lines[i])
                    if lines[i].strip().endswith("$$"):
                        i += 1
                        break
                    i += 1

            out_html.append(
                '<div style="text-align:center;margin:1em 0;overflow-x:auto">'
                + "\n".join(math_lines)
                + "</div>"
            )
            continue

        plain_lines.append(lines[i])
        i += 1

    _flush_plain()
    return "\n".join(out_html)


# ---------------------------------------------------------------------------
# Verdict helpers
# ---------------------------------------------------------------------------

def _verdict_label_ll(verdict: str | None) -> str:
    """Return human-readable Russian label for the LL verdict."""
    if verdict == "ll":
        return "LL(k)"
    if verdict == "not_ll":
        return "Не LL ни для какого k"
    if verdict == "failure":
        return "Ошибка выполнения"
    return "Неизвестно"


def _verdict_color(verdict: str | None) -> str:
    """Return hex color string for verdict badge."""
    if verdict == "ll":
        return "#2ecc71"
    if verdict == "not_ll":
        return "#e74c3c"
    return "#f39c12"


def _confidence_str(confidence: float | None) -> str:
    """Format confidence as percentage string."""
    if confidence is None:
        return ""
    return f"{int(confidence * 100)}%"


# ---------------------------------------------------------------------------
# Grammar helpers
# ---------------------------------------------------------------------------

def render_grammar_text(grammar: dict | None) -> str:
    """Render grammar as 'S → aAb | bBa\\nA → aA | ε' text.

    Grammar dict is expected to have 'rules' (list of {lhs, rhs}) and
    optionally 'start' indicating the start symbol.
    """
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

    lines: list[str] = []
    start = grammar.get("start", "")
    if start in by_lhs:
        alts = " | ".join(by_lhs[start])
        lines.append(f"{start} → {alts}")
    for nt, alts_list in by_lhs.items():
        if nt == start:
            continue
        alts = " | ".join(alts_list)
        lines.append(f"{nt} → {alts}")
    return "\n".join(lines)


def _render_grammar_html(grammar: dict | None) -> str:
    """Render grammar as an HTML <pre> block with monospace font."""
    text = render_grammar_text(grammar)
    if not text:
        return '<div class="ll-p" style="color:#888">Грамматика не предоставлена.</div>'
    return f'<pre class="ll-grammar">{_esc(text)}</pre>'


# ---------------------------------------------------------------------------
# FIRST/FOLLOW helpers
# ---------------------------------------------------------------------------

def render_first_follow_table_md(first_follow_result: dict | None) -> str:
    """Render FIRST/FOLLOW sets as a Markdown table.

    Expects first_follow_result to have 'first_sets' and 'follow_sets' keys,
    each mapping nonterminal names to sets/lists of terminal strings.
    """
    if not isinstance(first_follow_result, dict):
        return ""
    first_sets = first_follow_result.get("first_sets") or {}
    follow_sets = first_follow_result.get("follow_sets") or {}
    k = first_follow_result.get("k", 1)

    if not first_sets and not follow_sets:
        return ""

    all_nts = sorted(set(list(first_sets.keys()) + list(follow_sets.keys())))
    if not all_nts:
        return ""

    header = f"| Нетерминал | FIRST_{k} | FOLLOW_{k} |"
    sep = "|------------|----------|----------|"
    rows = [header, sep]
    for nt in all_nts:
        f_raw = first_sets.get(nt, [])
        fo_raw = follow_sets.get(nt, [])
        if isinstance(f_raw, (list, set)):
            f_str = "{ " + ", ".join(str(x) for x in sorted(f_raw)) + " }"
        else:
            f_str = str(f_raw)
        if isinstance(fo_raw, (list, set)):
            fo_str = "{ " + ", ".join(str(x) for x in sorted(fo_raw)) + " }"
        else:
            fo_str = str(fo_raw)
        rows.append(f"| {nt} | {f_str} | {fo_str} |")
    return "\n".join(rows)


def render_parse_table_md(first_follow_result: dict | None) -> str:
    """Render LL(k) parse table as a Markdown table.

    Expects first_follow_result to have 'parse_table' key, which maps
    (nonterminal, lookahead) pairs or nested dicts to production rules.
    """
    if not isinstance(first_follow_result, dict):
        return ""
    parse_table = first_follow_result.get("parse_table")
    if not parse_table:
        return ""

    # parse_table may be: {nt: {lookahead: rule_str}} or a flat dict with
    # string keys "(A, a)" → rule_str. Handle both shapes gracefully.
    if not isinstance(parse_table, dict):
        return ""

    # Detect nested shape: values are dicts
    first_val = next(iter(parse_table.values()), None)
    if isinstance(first_val, dict):
        # Nested: {nt: {lookahead: rule}}
        nts = sorted(parse_table.keys())
        all_lookaheads: list[str] = []
        seen_la: set[str] = set()
        for nt in nts:
            for la in parse_table[nt]:
                if la not in seen_la:
                    seen_la.add(la)
                    all_lookaheads.append(la)
        all_lookaheads = sorted(all_lookaheads)
        header = "| NT \\ Lookahead | " + " | ".join(all_lookaheads) + " |"
        sep = "|" + "---|" * (len(all_lookaheads) + 1)
        rows = [header, sep]
        for nt in nts:
            cells = []
            for la in all_lookaheads:
                rule = parse_table[nt].get(la, "")
                cells.append(str(rule) if rule is not None else "")
            rows.append(f"| {nt} | " + " | ".join(cells) + " |")
        return "\n".join(rows)
    else:
        # Flat: {"(A, a)": rule}
        header = "| Пара (NT, Lookahead) | Правило |"
        sep = "|----------------------|---------|"
        rows = [header, sep]
        for key, rule in sorted(parse_table.items()):
            rows.append(f"| {key} | {rule} |")
        return "\n".join(rows)


def render_conflicts_md(first_follow_result: dict | None) -> str:
    """Render LL conflicts as Markdown — used when is_ll_k=False.

    Expects first_follow_result to have 'conflicts' key, a list of
    conflict description dicts or strings.
    """
    if not isinstance(first_follow_result, dict):
        return ""
    conflicts = first_follow_result.get("conflicts") or []
    if not conflicts:
        return ""

    lines = ["### Конфликты таблицы разбора\n"]
    for i, c in enumerate(conflicts, 1):
        if isinstance(c, dict):
            nt = c.get("nonterminal", "?")
            la = c.get("lookahead", "?")
            rules = c.get("competing_rules", [])
            conflict_type = c.get("type", "")
            rule_str = " vs ".join(str(r) for r in rules) if rules else ""
            entry = f"{i}. **NT={nt}, lookahead={la}**"
            if rule_str:
                entry += f": `{rule_str}`"
            if conflict_type:
                entry += f" — {conflict_type}"
            lines.append(entry)
        else:
            lines.append(f"{i}. {c}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def render_markdown(result: dict) -> str:
    """Render result as exam-style Markdown.

    Structure:
    # Проверка LL-свойства

    **Задача:** {source_text}

    ## Вердикт: LL(k) / Не LL / Неизвестно

    **Уверенность:** 85%

    ## Доказательство

    [method-specific section]

    ## Грамматика (если LL)
    S → aAb | bBa
    A → aA | ε

    ## FIRST/FOLLOW таблица
    | Нетерминал | FIRST_1 | FOLLOW_1 |

    ## Таблица разбора

    ## Анализ агентов
    [list of agents and their verdicts]
    """
    if result is None:
        return "> Результат отсутствует. Нет данных для отображения.\n"

    sections: list[str] = []

    # Title
    source = result.get("source_text", "")
    sections.append("# Проверка LL-свойства\n")
    if source:
        sections.append(f"**Задача:** {source}\n")

    # Task type annotation
    task_type = result.get("task_type", "")
    if task_type:
        type_labels = {
            "ll_check_language": "Формат 1: проверка языка на LL",
            "ll_check_grammar_lang": "Формат 2: проверка LL-свойства языка с грамматикой",
            "ll_check_grammar": "Формат 3: проверка LL-свойства грамматики",
        }
        sections.append(f"*Тип задачи: {type_labels.get(task_type, task_type)}*\n")

    # Verdict
    verdict = result.get("verdict")
    conf = result.get("confidence")
    label = _verdict_label_ll(verdict)
    sections.append(f"## Вердикт: {label}\n")
    if conf is not None:
        sections.append(f"**Уверенность:** {_confidence_str(conf)}\n")

    k = result.get("k")
    if verdict == "ll" and k is not None:
        sections.append(f"**Минимальное k:** {k}\n")

    # Proof section
    proof = result.get("proof")
    if proof is not None:
        sections.append("## Доказательство\n")
        if isinstance(proof, str):
            sections.append(proof)
            sections.append("")
        elif isinstance(proof, dict):
            method = proof.get("method", "")
            if method:
                sections.append(f"**Метод:** {method}\n")
            details = proof.get("details") or {}
            if isinstance(details, dict) and details:
                # Render known detail fields
                summary = details.get("summary") or details.get("argument", "")
                if summary:
                    sections.append(f"{summary}\n")
                steps = details.get("steps") or []
                if steps:
                    sections.append("### Шаги\n")
                    for step in steps:
                        if not isinstance(step, dict):
                            sections.append(f"- {step}")
                            continue
                        num = step.get("step_number", "")
                        s_title = step.get("title", "")
                        content = step.get("content", "")
                        sections.append(f"**Шаг {num}. {s_title}**")
                        if content:
                            sections.append(f"{content}")
                    sections.append("")
                # Evidence / witness for not_ll proofs
                witness = details.get("witness") or details.get("word_chosen")
                if witness:
                    sections.append(f"> **Свидетель:** `{witness}`\n")
                evidence = details.get("evidence") or details.get("counterexample")
                if evidence and isinstance(evidence, str):
                    sections.append(f"> **Обоснование:** {evidence}\n")
                conclusion = details.get("conclusion")
                if conclusion:
                    sections.append(f"**Заключение:** {conclusion}\n")

    # Grammar section
    grammar = result.get("grammar")
    if grammar is not None:
        sections.append("## Грамматика\n")
        grammar_text = render_grammar_text(grammar)
        if grammar_text:
            sections.append("```")
            sections.append(grammar_text)
            sections.append("```\n")

    # FIRST/FOLLOW table
    ff_result = result.get("first_follow_result")
    if isinstance(ff_result, dict):
        is_ll_k = ff_result.get("is_ll_k")
        ff_table = render_first_follow_table_md(ff_result)
        if ff_table:
            sections.append("## FIRST/FOLLOW таблица\n")
            sections.append(ff_table)
            sections.append("")

        # Parse table if LL
        if is_ll_k:
            pt = render_parse_table_md(ff_result)
            if pt:
                sections.append("## Таблица разбора\n")
                sections.append(pt)
                sections.append("")
        else:
            # Conflicts if not LL
            conflicts_md = render_conflicts_md(ff_result)
            if conflicts_md:
                sections.append(conflicts_md)
                sections.append("")

    # Claim verification — per-agent map: {agent_name: {verification_status, issues, ...}}
    claim_ver = result.get("claim_verification")
    if isinstance(claim_ver, dict) and claim_ver:
        sections.append("## Проверка утверждений\n")
        for agent_name, vdata in claim_ver.items():
            if not isinstance(vdata, dict):
                continue
            vstatus = vdata.get("verification_status") or vdata.get("status", "?")
            vissues = vdata.get("issues") or []
            checks_ok = vdata.get("checks_passed", "?")
            checks_tot = vdata.get("checks_total", "?")
            line = f"- **{agent_name}**: {vstatus} ({checks_ok}/{checks_tot} проверок)"
            if vissues:
                line += " — " + "; ".join(str(i) for i in vissues[:2])
            sections.append(line)
        sections.append("")

    # Reasoning summary (formalizer's full proof or reasoning agent summary)
    reasoning = result.get("reasoning_summary")
    if reasoning:
        # If content already starts with a markdown header, don't add our own
        if not reasoning.lstrip().startswith("#"):
            sections.append("## Решение\n")
        sections.append(f"{reasoning}\n")

    # Reasoning agent recommendations
    reasoning_out = result.get("reasoning_output") or {}
    if isinstance(reasoning_out, dict):
        ra_summary = reasoning_out.get("summary")
        ra_just = reasoning_out.get("justification")
        ra_pm = reasoning_out.get("primary_method")
        ra_pa = reasoning_out.get("primary_agent")
        if ra_summary or ra_just:
            sections.append("## Рекомендации агента-рассуждателя\n")
            meta = []
            if ra_pa:
                meta.append(f"агент: **{ra_pa}**")
            if ra_pm:
                meta.append(f"метод: **{ra_pm}**")
            if meta:
                sections.append(f"*{', '.join(meta)}*\n")
            if ra_summary:
                sections.append(f"{ra_summary}\n")
            if ra_just:
                sections.append(f"**Обоснование:** {ra_just}\n")

    # Agents used
    agents = result.get("agents_used") or []
    if agents:
        sections.append("## Анализ агентов\n")
        sections.append(f"**Агенты (успешно):** {', '.join(agents)}\n")

    # Specialist outputs — verdict + proof_sketch details
    spec_outputs = result.get("specialist_outputs") or {}
    if isinstance(spec_outputs, dict) and spec_outputs:
        for agent_name, agent_out in spec_outputs.items():
            if not isinstance(agent_out, dict):
                continue
            agent_verdict = agent_out.get("verdict")
            agent_conf = agent_out.get("confidence")
            line = f"- **{agent_name}**"
            if agent_verdict:
                line += f": вердикт `{agent_verdict}`"
            if agent_conf is not None:
                line += f", уверенность {_confidence_str(agent_conf)}"
            sections.append(line)
            ps = agent_out.get("proof_sketch")
            if isinstance(ps, dict):
                method = ps.get("method")
                if method:
                    sections.append(f"  - Метод: `{method}`")
                desc = ps.get("description") or ps.get("argument")
                if desc:
                    sections.append(f"  - {desc}")
                witness = ps.get("witness")
                if isinstance(witness, dict):
                    for wk, wv in list(witness.items())[:4]:
                        sections.append(f"  - {wk}: {wv}")
                elif witness:
                    sections.append(f"  - Свидетель: {witness}")
        sections.append("")

    # Failed agents
    failed = result.get("agents_failed") or []
    if failed:
        sections.append(f"**Агенты (с ошибками):** {len(failed)}\n")
        for f in failed:
            if isinstance(f, dict):
                name = f.get("agent", "?")
                err = f.get("error", "")
                sections.append(f"- `{name}` — {err}")
        sections.append("")

    retries = result.get("retries", 0)
    if retries:
        sections.append(f"**Повторных попыток:** {retries}\n")

    # Errors
    errs = result.get("errors") or []
    if errs:
        sections.append(f"### Ошибки пайплайна ({len(errs)})\n")
        for e in errs:
            sections.append(f"- {e}")
        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# HTML renderer helpers
# ---------------------------------------------------------------------------

_HTML_CSS = """\
body{font-family:'Segoe UI',system-ui,sans-serif;max-width:800px;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#2c3e50;background:#ecf0f1}
.ll-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:20px;padding:16px 20px;background:#2c3e50;color:#ecf0f1;border-radius:8px}
.ll-head-text{flex:1}
.ll-task-label{font-size:12px;color:#bdc3c7;margin-bottom:4px}
.ll-task{font-size:15px;line-height:1.6;color:#ecf0f1}
.ll-badge{font-size:13px;font-weight:700;padding:6px 16px;border-radius:8px;white-space:nowrap;flex-shrink:0;align-self:flex-start}
.ll-sec{font-size:16px;font-weight:600;color:#2c3e50;margin:24px 0 10px;padding-top:16px;border-top:1px solid #bdc3c7}
.ll-sec:first-of-type{border-top:none;padding-top:0}
.ll-p{font-size:14px;line-height:1.75;margin:6px 0}
.ll-meta{font-size:12px;color:#7f8c8d;margin:4px 0 10px}
.ll-box{background:#fff;border-radius:6px;padding:12px 16px;margin:10px 0;font-size:13px;line-height:1.7;border:1px solid #dfe6e9}
.ll-mono,.ll-grammar{font-family:Consolas,'Courier New',monospace;font-size:13px}
.ll-grammar{background:#fff;border-radius:6px;padding:12px 16px;border:1px solid #dfe6e9;margin:10px 0;white-space:pre;overflow-x:auto}
.ll-tab{display:flex;gap:2px;margin:16px 0 0;flex-wrap:wrap;border-bottom:2px solid #bdc3c7}
.ll-tab-btn{font-size:12px;padding:7px 16px;border-radius:6px 6px 0 0;background:#dfe6e9;color:#636e72;cursor:pointer;border:none;font-family:inherit;margin-bottom:-2px;border-bottom:2px solid transparent}
.ll-tab-btn:hover{background:#b2bec3}
.ll-tab-btn.active{background:#fff;color:#2c3e50;font-weight:600;border-bottom-color:#2980b9}
.ll-panel{display:none;padding:14px 0}.ll-panel.active{display:block}
.ll-step{display:flex;gap:10px;margin:10px 0}
.ll-n{min-width:24px;height:24px;border-radius:50%;background:#dfe6e9;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#636e72;flex-shrink:0;margin-top:2px}
.ll-t{font-size:14px;line-height:1.7}
.ll-footer{display:flex;gap:16px;align-items:center;margin-top:20px;padding-top:14px;border-top:1px solid #bdc3c7;font-size:12px;color:#7f8c8d;flex-wrap:wrap}
.ll-pill{font-size:11px;padding:2px 8px;border-radius:6px;background:#d6eaf8;color:#1a5276}
.ll-pill-warn{background:#fadbd8;color:#922b21}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}
th,td{border:1px solid #bdc3c7;padding:6px 10px;text-align:left}
th{background:#dfe6e9;font-weight:600}
tr:nth-child(even) td{background:#f8f9fa}
"""

_HTML_JS = """\
function llShowTab(n){
  document.querySelectorAll('.ll-tab-btn').forEach((b,i)=>b.classList.toggle('active',i===n));
  document.querySelectorAll('.ll-panel').forEach((p,i)=>p.classList.toggle('active',i===n));
  if(window.renderMathInElement){
    try{window.renderMathInElement(document.querySelector('.ll-panel.active'),{
      delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},
                  {left:'\\\\[',right:'\\\\]',display:true},{left:'\\\\(',right:'\\\\)',display:false}],
      throwOnError:false});
    }catch(e){}
  }
}
"""


def _render_ff_table_html(ff_result: dict) -> str:
    """Render FIRST/FOLLOW sets as an HTML table."""
    first_sets = ff_result.get("first_sets") or {}
    follow_sets = ff_result.get("follow_sets") or {}
    k = ff_result.get("k", 1)
    all_nts = sorted(set(list(first_sets.keys()) + list(follow_sets.keys())))
    if not all_nts:
        return ""
    rows = [f'<table><tr><th>Нетерминал</th><th>FIRST<sub>{k}</sub></th><th>FOLLOW<sub>{k}</sub></th></tr>']
    for nt in all_nts:
        f_raw = first_sets.get(nt, [])
        fo_raw = follow_sets.get(nt, [])
        f_str = "{ " + ", ".join(_esc(str(x)) for x in sorted(f_raw)) + " }" if isinstance(f_raw, (list, set)) else _esc(str(f_raw))
        fo_str = "{ " + ", ".join(_esc(str(x)) for x in sorted(fo_raw)) + " }" if isinstance(fo_raw, (list, set)) else _esc(str(fo_raw))
        rows.append(f'<tr><td><span class="ll-mono">{_esc(nt)}</span></td><td>{f_str}</td><td>{fo_str}</td></tr>')
    rows.append('</table>')
    return "\n".join(rows)


def _render_parse_table_html(ff_result: dict) -> str:
    """Render LL parse table as an HTML table."""
    parse_table = ff_result.get("parse_table")
    if not parse_table or not isinstance(parse_table, dict):
        return ""
    first_val = next(iter(parse_table.values()), None)
    if isinstance(first_val, dict):
        nts = sorted(parse_table.keys())
        all_lookaheads: list[str] = []
        seen_la: set[str] = set()
        for nt in nts:
            for la in parse_table[nt]:
                if la not in seen_la:
                    seen_la.add(la)
                    all_lookaheads.append(la)
        all_lookaheads = sorted(all_lookaheads)
        header_cells = "".join(f"<th>{_esc(la)}</th>" for la in all_lookaheads)
        rows = [f'<table><tr><th>NT \\ Lookahead</th>{header_cells}</tr>']
        for nt in nts:
            cells = ""
            for la in all_lookaheads:
                rule = parse_table[nt].get(la, "")
                cells += f"<td><span class='ll-mono'>{_esc(str(rule)) if rule else ''}</span></td>"
            rows.append(f'<tr><td><span class="ll-mono">{_esc(nt)}</span></td>{cells}</tr>')
        rows.append('</table>')
        return "\n".join(rows)
    else:
        rows = ['<table><tr><th>Пара (NT, Lookahead)</th><th>Правило</th></tr>']
        for key, rule in sorted(parse_table.items()):
            rows.append(f'<tr><td><span class="ll-mono">{_esc(str(key))}</span></td>'
                        f'<td><span class="ll-mono">{_esc(str(rule))}</span></td></tr>')
        rows.append('</table>')
        return "\n".join(rows)


def _render_conflicts_html(ff_result: dict) -> str:
    """Render LL conflicts as HTML."""
    conflicts = ff_result.get("conflicts") or []
    if not conflicts:
        return ""
    parts = ['<div class="ll-p"><strong>Конфликты таблицы разбора:</strong></div><ul>']
    for c in conflicts:
        if isinstance(c, dict):
            nt = _esc(c.get("nonterminal", "?"))
            la = _esc(c.get("lookahead", "?"))
            rules = c.get("competing_rules", [])
            conflict_type = _esc(c.get("type", ""))
            rule_str = " vs ".join(_esc(str(r)) for r in rules) if rules else ""
            item = f"<strong>NT={nt}, lookahead={la}</strong>"
            if rule_str:
                item += f': <span class="ll-mono">{rule_str}</span>'
            if conflict_type:
                item += f" — {conflict_type}"
            parts.append(f"<li>{item}</li>")
        else:
            parts.append(f"<li>{_esc(str(c))}</li>")
    parts.append("</ul>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def render_html(result: dict) -> str:
    """Render result as HTML with KaTeX, tabs, styled.

    - Uses KaTeX CDN for LaTeX rendering
    - Tabs: "Решение", "Доказательство", "Детали", "Грамматика/Таблица"
    - Color-coded verdict: green (ll), red (not_ll), yellow (uncertain)
    - Grammar formatted as S → α | β
    - FIRST/FOLLOW as HTML table
    - Responsive design, exam-style

    CRITICAL: Always guard `if result.get("proof") is not None:` before
    accessing proof fields (known bug pattern).
    """
    if result is None:
        return "<html><body><p>Результат отсутствует</p></body></html>"

    verdict = result.get("verdict")
    source = _esc(result.get("source_text", ""))
    conf = result.get("confidence")
    k = result.get("k")
    task_type = result.get("task_type", "")

    badge_bg = _verdict_color(verdict)
    badge_text_color = "#fff"

    if verdict == "ll":
        badge_label = f"LL({k})" if k is not None else "LL(k)"
    elif verdict == "not_ll":
        badge_label = "Не LL"
    elif verdict == "failure":
        badge_label = "Ошибка"
    else:
        badge_label = "Неизвестно"

    type_labels = {
        "ll_check_language": "Формат 1: язык",
        "ll_check_grammar_lang": "Формат 2: язык + грамматика",
        "ll_check_grammar": "Формат 3: грамматика",
    }
    task_type_label = type_labels.get(task_type, task_type)

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append("<html lang='ru'><head><meta charset='utf-8'>")
    parts.append("<title>LL — Результат анализа</title>")
    parts.append("<style>")
    parts.append(_HTML_CSS)
    parts.append("</style></head><body>")

    # --- Header ---
    parts.append('<div class="ll-head">')
    parts.append('  <div class="ll-head-text">')
    if task_type_label:
        parts.append(f'    <div class="ll-task-label">Задача (проверка LL-свойства · {_esc(task_type_label)})</div>')
    else:
        parts.append('    <div class="ll-task-label">Задача (проверка LL-свойства)</div>')
    parts.append(f'    <div class="ll-task">{source if source else "—"}</div>')
    parts.append('  </div>')
    parts.append(
        f'  <span class="ll-badge" '
        f'style="background:{badge_bg};color:{badge_text_color}">'
        f'{_esc(badge_label)}</span>'
    )
    parts.append('</div>')

    # --- Confidence line ---
    if conf is not None:
        try:
            conf_pct = int(float(conf) * 100)
            parts.append(f'<div class="ll-meta">Уверенность: <strong>{conf_pct}%</strong></div>')
        except (TypeError, ValueError):
            pass

    # --- Tabs ---
    tabs: list[tuple[str, str]] = []  # (label, html_content)

    # Tab 0: Решение — verdict summary
    sol_parts: list[str] = []
    sol_parts.append(f'<div class="ll-p"><strong>Вердикт:</strong> {_esc(_verdict_label_ll(verdict))}</div>')
    if verdict == "ll" and k is not None:
        sol_parts.append(f'<div class="ll-p"><strong>Минимальное k:</strong> {_esc(str(k))}</div>')

    ff_result = result.get("first_follow_result")
    if isinstance(ff_result, dict):
        is_ll_k = ff_result.get("is_ll_k")
        oracle_k = ff_result.get("k")
        if is_ll_k is not None:
            oracle_status = f"LL({oracle_k})" if is_ll_k else f"Не LL({oracle_k})"
            sol_parts.append(f'<div class="ll-p"><strong>Oracle (FIRST/FOLLOW):</strong> {_esc(oracle_status)}</div>')

    # Reasoning agent primary info
    reasoning_out = result.get("reasoning_output") or {}
    if isinstance(reasoning_out, dict):
        ra_pm = reasoning_out.get("primary_method")
        ra_pa = reasoning_out.get("primary_agent")
        if ra_pm or ra_pa:
            meta_items = []
            if ra_pa:
                meta_items.append(f'Агент: <strong>{_esc(str(ra_pa))}</strong>')
            if ra_pm:
                meta_items.append(f'Метод: <strong>{_esc(str(ra_pm))}</strong>')
            sol_parts.append(f'<div class="ll-meta" style="margin-top:8px">{" · ".join(meta_items)}</div>')

    # Formalizer solution rendered as HTML (not raw-escaped)
    reasoning = result.get("reasoning_summary")
    if reasoning:
        sol_parts.append(f'<div class="ll-box">{_render_md_block(reasoning)}</div>')

    tabs.append(("Решение", "\n".join(sol_parts)))

    # Tab 1: Доказательство
    proof = result.get("proof")
    if proof is not None:
        proof_parts: list[str] = []
        if isinstance(proof, str):
            proof_parts.append(f'<div class="ll-box" style="white-space:pre-wrap">{_esc(proof)}</div>')
        elif isinstance(proof, dict):
            method = proof.get("method", "")
            if method:
                proof_parts.append(f'<div class="ll-p"><strong>Метод:</strong> <span class="ll-mono">{_esc(method)}</span></div>')
            details = proof.get("details") or {}
            if isinstance(details, dict):
                summary = details.get("summary") or details.get("argument", "")
                if summary:
                    proof_parts.append(f'<div class="ll-box">{_esc(summary)}</div>')
                steps = details.get("steps") or []
                if steps:
                    for i, step in enumerate(steps, 1):
                        if not isinstance(step, dict):
                            proof_parts.append(f'<div class="ll-step"><div class="ll-n">{i}</div>'
                                               f'<div class="ll-t">{_esc(str(step))}</div></div>')
                            continue
                        num = step.get("step_number", i)
                        s_title = _esc(step.get("title", ""))
                        content = _esc(step.get("content", ""))
                        just = _esc(step.get("justification", ""))
                        proof_parts.append(
                            f'<div class="ll-step"><div class="ll-n">{_esc(str(num))}</div>'
                            f'<div class="ll-t"><strong>{s_title}</strong>'
                            + (f'<br>{content}' if content else "")
                            + (f'<br><em style="color:#7f8c8d">{just}</em>' if just else "")
                            + '</div></div>'
                        )
                witness = details.get("witness") or details.get("word_chosen")
                if witness:
                    proof_parts.append(f'<div class="ll-box"><strong>Свидетель:</strong> <span class="ll-mono">{_esc(str(witness))}</span></div>')
                evidence = details.get("evidence") or details.get("counterexample")
                if evidence and isinstance(evidence, str):
                    proof_parts.append(f'<div class="ll-p"><strong>Обоснование:</strong> {_esc(evidence)}</div>')
                conclusion = details.get("conclusion")
                if conclusion:
                    proof_parts.append(f'<div class="ll-box" style="border-left:4px solid {badge_bg}"><strong>Заключение.</strong> {_esc(conclusion)}</div>')
        tabs.append(("Доказательство", "\n".join(proof_parts)))

    # Tab 2: Грамматика/Таблица
    grammar = result.get("grammar")
    gt_parts: list[str] = []
    if grammar is not None:
        gt_parts.append('<div class="ll-p"><strong>LL-грамматика G:</strong></div>')
        gt_parts.append(_render_grammar_html(grammar))

    if isinstance(ff_result, dict):
        ff_html = _render_ff_table_html(ff_result)
        if ff_html:
            k_val = ff_result.get("k", 1)
            gt_parts.append(f'<div class="ll-p" style="margin-top:16px"><strong>Таблица FIRST<sub>{k_val}</sub>/FOLLOW<sub>{k_val}</sub>:</strong></div>')
            gt_parts.append(ff_html)

        is_ll_k = ff_result.get("is_ll_k")
        if is_ll_k:
            pt_html = _render_parse_table_html(ff_result)
            if pt_html:
                gt_parts.append('<div class="ll-p" style="margin-top:16px"><strong>Таблица разбора LL(k):</strong></div>')
                gt_parts.append(pt_html)
        else:
            conf_html = _render_conflicts_html(ff_result)
            if conf_html:
                gt_parts.append('<div class="ll-p" style="margin-top:16px"></div>')
                gt_parts.append(conf_html)

    if gt_parts:
        tabs.append(("Грамматика/Таблица", "\n".join(gt_parts)))

    # Tab 3: Детали
    details_parts: list[str] = []

    claim_ver = result.get("claim_verification")
    if isinstance(claim_ver, dict) and claim_ver:
        details_parts.append('<div class="ll-p"><strong>Проверка утверждений:</strong></div><ul>')
        for agent_name, vdata in claim_ver.items():
            if not isinstance(vdata, dict):
                continue
            vstatus = vdata.get("verification_status") or vdata.get("status", "?")
            checks_ok = vdata.get("checks_passed", "?")
            checks_tot = vdata.get("checks_total", "?")
            vissues = vdata.get("issues") or []
            status_color = {
                "verified": "#27ae60",
                "refuted": "#e74c3c",
                "inconclusive": "#f39c12",
            }.get(vstatus, "#7f8c8d")
            item = (
                f'<span class="ll-mono" style="font-weight:600">{_esc(agent_name)}</span>: '
                f'<span style="color:{status_color};font-weight:600">{_esc(vstatus)}</span>'
                f' ({_esc(str(checks_ok))}/{_esc(str(checks_tot))} проверок)'
            )
            if vissues:
                item += f' — <span style="color:#7f8c8d;font-size:12px">{_esc("; ".join(str(i) for i in vissues[:2]))}</span>'
            details_parts.append(f'<li>{item}</li>')
        details_parts.append('</ul>')

    spec_outputs = result.get("specialist_outputs") or {}
    if isinstance(spec_outputs, dict) and spec_outputs:
        details_parts.append('<div class="ll-p" style="margin-top:12px"><strong>Вывод специалистов:</strong></div>')
        for agent_name, agent_out in spec_outputs.items():
            if not isinstance(agent_out, dict):
                continue
            agent_verdict = agent_out.get("verdict")
            agent_conf = agent_out.get("confidence")
            v_color = _verdict_color(agent_verdict)
            conf_str = ""
            if agent_conf is not None:
                try:
                    conf_str = f' <span style="color:#7f8c8d;font-size:12px">({int(float(agent_conf) * 100)}%)</span>'
                except (TypeError, ValueError):
                    pass
            header = (
                f'<div style="display:flex;align-items:center;gap:8px;margin-top:10px">'
                f'<span class="ll-mono" style="font-weight:600">{_esc(agent_name)}</span>'
            )
            if agent_verdict:
                header += (
                    f'<span style="font-size:11px;padding:2px 8px;border-radius:4px;'
                    f'background:{v_color};color:#fff">{_esc(str(agent_verdict))}</span>'
                )
            header += f'{conf_str}</div>'
            details_parts.append(header)
            ps = agent_out.get("proof_sketch")
            if isinstance(ps, dict):
                ps_items: list[str] = []
                method = ps.get("method")
                if method:
                    ps_items.append(f'<strong>Метод:</strong> <code>{_esc(str(method))}</code>')
                desc = ps.get("description") or ps.get("argument")
                if desc:
                    ps_items.append(_render_prose_block(str(desc)))
                witness = ps.get("witness")
                if isinstance(witness, dict):
                    w_lines = []
                    for wk, wv in list(witness.items())[:4]:
                        w_lines.append(f'<li><em>{_esc(str(wk))}</em>: {_esc(str(wv))}</li>')
                    if w_lines:
                        ps_items.append(f'<strong>Свидетель:</strong><ul>{"".join(w_lines)}</ul>')
                elif witness:
                    ps_items.append(f'<strong>Свидетель:</strong> <span class="ll-mono">{_esc(str(witness))}</span>')
                if ps_items:
                    details_parts.append(f'<div class="ll-box" style="margin-top:4px">{"".join(ps_items)}</div>')

    # Reasoning agent advice
    reasoning_out = result.get("reasoning_output") or {}
    if isinstance(reasoning_out, dict) and (reasoning_out.get("summary") or reasoning_out.get("justification")):
        details_parts.append('<div class="ll-p" style="margin-top:16px"><strong>Советы агента-рассуждателя:</strong></div>')
        ra_summary = reasoning_out.get("summary")
        if ra_summary:
            details_parts.append(f'<div class="ll-box" style="border-left:4px solid #2980b9">{_render_prose_block(str(ra_summary))}</div>')
        ra_just = reasoning_out.get("justification")
        if ra_just:
            details_parts.append(f'<div class="ll-p"><strong>Обоснование:</strong></div>')
            details_parts.append(f'<div class="ll-box">{_render_prose_block(str(ra_just))}</div>')

    retries = result.get("retries", 0)
    if retries:
        details_parts.append(f'<div class="ll-meta">Повторных попыток: {_esc(str(retries))}</div>')

    if details_parts:
        tabs.append(("Детали", "\n".join(details_parts)))

    # Render tabs
    if tabs:
        parts.append('<div class="ll-sec">Результат</div>')
        parts.append('<div class="ll-tab">')
        for i, (label, _) in enumerate(tabs):
            active = " active" if i == 0 else ""
            parts.append(f'  <button class="ll-tab-btn{active}" onclick="llShowTab({i})">{_esc(label)}</button>')
        parts.append('</div>')
        for i, (_, content) in enumerate(tabs):
            active = " active" if i == 0 else ""
            parts.append(f'<div class="ll-panel{active}" id="ll-panel-{i}">')
            parts.append(content)
            parts.append('</div>')

    # --- Failed agents ---
    failed = result.get("agents_failed") or []
    if failed:
        parts.append(f'<div class="ll-sec">Агенты с ошибками ({len(failed)})</div><ul>')
        for f in failed:
            if isinstance(f, dict):
                name = _esc(f.get("agent", "?"))
                err = _esc(f.get("error", ""))
                parts.append(f'<li><code>{name}</code> — {err}</li>')
        parts.append('</ul>')

    # --- Pipeline errors ---
    errs = result.get("errors") or []
    if errs:
        parts.append(f'<div class="ll-sec">Ошибки пайплайна ({len(errs)})</div><ul>')
        for e in errs:
            parts.append(f'<li style="color:#c0392b">⚠ {_esc(str(e))}</li>')
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
        footer_items.append(f'<span class="ll-pill">{len(agents_used)} агентов отработали</span>')
    if failed:
        footer_items.append(f'<span class="ll-pill ll-pill-warn">{len(failed)} упали</span>')

    if footer_items:
        parts.append('<div class="ll-footer">')
        for item in footer_items:
            parts.append(f'  {item}')
        parts.append('</div>')

    # JS
    parts.append('<script>')
    parts.append(_HTML_JS)
    parts.append('</script>')

    # Conditional CDN: KaTeX
    html_so_far = "\n".join(parts)
    needs_katex = bool(
        re.search(r'\$\$|\\\(|\\\[', html_so_far)
        or re.search(r'\$[^$\n]{1,200}\$', html_so_far)
    )
    if needs_katex:
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
            "onload=\"renderMathInElement(document.body,{delimiters:["
            "{left:'$$',right:'$$',display:true},"
            "{left:'$',right:'$',display:false},"
            "{left:'\\\\[',right:'\\\\]',display:true},"
            "{left:'\\\\(',right:'\\\\)',display:false}"
            '],throwOnError:false});"></script>'
        )

    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Lean 4 stub renderer
# ---------------------------------------------------------------------------

def render_lean(result: dict) -> str:
    """Render Lean 4 proof stub.

    For LL verdict: stub theorem LLGrammar with sorry
    For not_ll verdict: stub theorem NotLL with sorry
    Includes comments explaining what needs to be proved.
    """
    if result is None:
        return "-- Результат отсутствует\n"

    source = result.get("source_text", "")
    verdict = result.get("verdict")
    k = result.get("k")
    proof = result.get("proof")
    method = ""
    if proof is not None and isinstance(proof, dict):
        method = proof.get("method", "")

    lines: list[str] = []
    lines.append("-- Lean 4 stub (автоматически сгенерировано, требует доработки)")
    if source:
        lines.append(f"-- Язык: {source}")

    if verdict == "ll":
        k_label = str(k) if k is not None else "k"
        lines.append(f"-- Вердикт: LL({k_label})")
        lines.append("")
        lines.append("import Mathlib")
        lines.append("")
        lines.append("-- TODO: определить язык L как формальный язык над конечным алфавитом")
        lines.append("-- TODO: определить грамматику G и доказать, что L(G) = L")
        lines.append(f"-- TODO: доказать, что G является LL({k_label})-грамматикой:")
        lines.append("--   для каждого нетерминала A и каждой пары различных продукций A → α | β")
        lines.append(f"--   FIRST_{k_label}(α · FOLLOW_{k_label}(A)) ∩ FIRST_{k_label}(β · FOLLOW_{k_label}(A)) = ∅")
        lines.append("")
        lines.append("-- TODO: formalize LL grammar definition")
        lines.append("theorem grammar_is_ll : True := by")
        lines.append("  sorry")

    elif verdict == "not_ll":
        lines.append("-- Вердикт: не LL(k) ни для какого k")
        if method:
            lines.append(f"-- Метод: {method}")
        lines.append("")
        lines.append("import Mathlib")
        lines.append("")
        lines.append("-- TODO: определить язык L как формальный язык над конечным алфавитом")
        lines.append("-- TODO: доказать, что L не является LL(k) ни для какого k")
        if method == "substitution":
            lines.append("-- Подход: метод подстановки (substitution method)")
            lines.append("--   Пусть G — произвольная КС-грамматика порождающая L.")
            lines.append("--   Заменить нетерминал подходящим языком и получить противоречие.")
        elif method == "ll_grammar_construction":
            lines.append("-- Подход: конструктивное построение LL-грамматики дало конфликты.")
            lines.append("--   Показать, что любая грамматика для L имеет FIRST/FOLLOW конфликт.")
        else:
            lines.append("-- Подход: показать, что для любой грамматики G с L(G) = L")
            lines.append("--   таблица разбора LL(k) содержит конфликт для всех k ∈ ℕ.")
        lines.append("")
        lines.append("-- TODO: formalize not-LL proof")
        lines.append("theorem language_not_ll : True := by")
        lines.append("  sorry")

    elif verdict == "failure":
        lines.append("-- Вердикт: ошибка выполнения пайплайна")
        lines.append("")
        lines.append("import Mathlib")
        lines.append("")
        lines.append("-- Анализ не был завершён. Lean stub не может быть сформирован.")
        lines.append("theorem placeholder : True := by")
        lines.append("  trivial")

    else:
        # uncertain / unknown
        lines.append("-- Вердикт: неизвестно")
        lines.append("")
        lines.append("import Mathlib")
        lines.append("")
        lines.append("-- Недостаточно данных для формализации.")
        lines.append("-- TODO: уточнить задачу и повторить анализ.")
        lines.append("theorem placeholder : True := by")
        lines.append("  trivial")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def render_result(
    result: dict,
    output_dir: str | None = None,
    task_name: str | None = None,
) -> dict[str, str]:
    """Render pipeline result into all 4 formats.

    Returns dict: {"json": str, "markdown": str, "html": str, "lean": str}
    If output_dir provided: writes result.json, result.md, result.html, result.lean
    (or task_name.json / task_name.md / task_name.html / task_name.lean if task_name given).
    """
    # JSON
    json_str = json.dumps(result, ensure_ascii=False, indent=2, default=str)

    # Markdown
    md_str = render_markdown(result)

    # HTML
    html_str = render_html(result)

    # Lean
    lean_str = render_lean(result)

    outputs = {
        "json": json_str,
        "markdown": md_str,
        "html": html_str,
        "lean": lean_str,
    }

    if output_dir is not None:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        stem = task_name if task_name else "result"
        (out_path / f"{stem}.json").write_text(json_str, encoding="utf-8")
        (out_path / f"{stem}.md").write_text(md_str, encoding="utf-8")
        (out_path / f"{stem}.html").write_text(html_str, encoding="utf-8")
        (out_path / f"{stem}.lean").write_text(lean_str, encoding="utf-8")

    return outputs
