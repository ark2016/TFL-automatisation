"""
Renderer — format pipeline results as Markdown and HTML.

Takes a pipeline result dict and produces human-readable output
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
    if verdict == "regular":
        return "regular"
    if verdict == "non_regular":
        return "non_regular"
    return "unknown"


def _verdict_css_class(verdict: str | None) -> str:
    if verdict == "regular":
        return "verdict-regular"
    if verdict == "non_regular":
        return "verdict-non-regular"
    return "verdict-unknown"


# ---------------------------------------------------------------------------
# render_dfa_table
# ---------------------------------------------------------------------------

def render_dfa_table(dfa: dict) -> str:
    """Render a DFA transition table as a Markdown table.

    Expected *dfa* keys:
        states      – list of state names
        start       – start state name
        accept      – list/set of accepting state names
        alphabet    – list of input symbols
        transitions – dict mapping (state, symbol)->next_state
                      or nested dict state->{symbol: next_state}
    """
    states = dfa.get("states", [])
    start = dfa.get("start")
    accept = set(dfa.get("accept", []))
    alphabet = sorted(dfa.get("alphabet", []))
    transitions = dfa.get("transitions", {})

    # Build header
    header_cols = ["State"] + [str(s) for s in alphabet] + ["Accept"]
    header = "| " + " | ".join(header_cols) + " |"
    sep = "|" + "|".join("---" for _ in header_cols) + "|"

    rows: list[str] = []
    for st in states:
        name = f"\u2192{st}" if st == start else str(st)
        cells = [name]
        for sym in alphabet:
            # support nested dict or tuple-key dict
            if isinstance(transitions, dict):
                nxt = transitions.get((st, sym))
                if nxt is None and isinstance(transitions.get(st), dict):
                    nxt = transitions[st].get(sym)
            else:
                nxt = ""
            cells.append(str(nxt) if nxt is not None else "")
        cells.append("\u2713" if st in accept else "")
        rows.append("| " + " | ".join(cells) + " |")

    return "\n".join([header, sep] + rows)


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

def render_markdown(result: dict) -> str:
    """Convert pipeline result dict to Markdown."""
    evidence: dict = result.get("evidence") or {}
    lines: list[str] = []

    def _add(text: str = "") -> None:
        lines.append(text)

    # Title
    _add("# Результат анализа языка")
    _add()

    # --- Задача ---
    source_text = None
    ir = evidence.get("ir") or evidence.get("source_text")
    if isinstance(ir, dict):
        source_text = ir.get("source_text")
    elif isinstance(ir, str):
        source_text = ir
    if source_text:
        _add("## Задача")
        _add(str(source_text))
        _add()

    # --- Гипотеза ---
    hyp = evidence.get("hypothesis")
    if hyp:
        _add("## Гипотеза")
        _add(f"**Вердикт:** {hyp.get('hypothesis', hyp.get('verdict', 'unknown'))}  ")
        _add(f"**Уверенность:** {hyp.get('confidence', 'N/A')}  ")
        agents = hyp.get("suggested_agents", hyp.get("agents", []))
        if agents:
            _add(f"**Предложенные агенты:** {', '.join(agents)}")
        _add()

        atoms = hyp.get("atoms", [])
        if atoms:
            _add("### Атомы анализа")
            _add("| Описание | Тип памяти | Причина |")
            _add("|----------|-----------|---------|")
            for atom in atoms:
                desc = atom.get("description", "")
                mem = atom.get("memory_type", "")
                reason = atom.get("reason", "")
                _add(f"| {desc} | {mem} | {reason} |")
            _add()

    # --- Классификация ---
    clf = evidence.get("classifier")
    if clf:
        clf_evidence = clf.get("evidence", clf)
        _add("## Классификация")
        _add(f"**Вердикт:** {clf_evidence.get('verdict', clf.get('verdict', 'unknown'))}  ")
        _add(f"**Обоснование:** {clf_evidence.get('reasoning', clf.get('reasoning', ''))}")
        _add()

    # --- Доказательство ---
    has_proof = any(evidence.get(k) for k in (
        "re_builder", "dfa_builder", "pumping", "nerode", "closure",
    ))
    if has_proof:
        _add("## Доказательство")
        _add()

    # Regex
    reb = evidence.get("re_builder")
    if reb:
        reb_ev = reb.get("evidence", reb)
        _add("### Регулярное выражение")
        _add(f"`{reb_ev.get('regex', '')}` — {reb_ev.get('explanation', '')}")
        _add()

    # DFA
    dfa_ev = evidence.get("dfa_builder")
    if dfa_ev:
        dfab_ev = dfa_ev.get("evidence", dfa_ev)
        _add("### ДКА")
        dfa_obj = dfab_ev.get("dfa")
        if dfa_obj:
            _add(f"- Состояния: {dfa_obj.get('states', [])}")
            _add(f"- Начальное: {dfa_obj.get('start', '')}")
            _add(f"- Допускающие: {dfa_obj.get('accept', [])}")
            expl = dfab_ev.get("explanation", "")
            if expl:
                _add(f"- {expl}")
            _add()
            _add(render_dfa_table(dfa_obj))
        else:
            expl = dfab_ev.get("explanation", "")
            if expl:
                _add(expl)
        _add()

    # Pumping lemma
    pump = evidence.get("pumping")
    if pump:
        pump_ev = pump.get("evidence", pump)
        proof = pump_ev.get("proof") or {}
        _add("### Лемма о накачке")
        # Show failure message if agent failed
        if pump.get("status") == "failure" or pump_ev.get("status") == "failure":
            err = pump_ev.get("errors") or pump.get("errors") or []
            if isinstance(err, list):
                err = "; ".join(str(e) for e in err)
            _add(f"*{err}*")
            _add()
            pump = None  # skip proof parsing
    if pump:
        pump_ev = pump.get("evidence", pump)
        proof = pump_ev.get("proof") or {}
        # Word choice
        wc = proof.get("word_choice", {})
        word = wc.get("word", "")
        membership = wc.get("membership_argument", "")
        if word:
            _add(f"**Выбор слова:** w = {word}")
            _add()
        if membership:
            _add(f"**Принадлежность:** {membership}")
            _add()
        # Cut analysis
        cut = proof.get("cut_analysis", {})
        cut_arg = cut.get("argument", "")
        cases = cut.get("cases", [])
        if cut_arg:
            _add(f"**Анализ разбиения:** {cut_arg}")
            _add()
        for case in cases:
            if isinstance(case, dict):
                desc = case.get("description", "")
                pumped = case.get("pumped_word", "")
                contradiction = case.get("contradiction", "")
                if desc:
                    _add(f"- **{desc}**")
                if pumped:
                    _add(f"  Накачанное слово: {pumped}")
                if contradiction:
                    _add(f"  Противоречие: {contradiction}")
            else:
                _add(f"- {case}")
        # Conclusion
        conclusion = proof.get("conclusion", "")
        if conclusion:
            _add()
            _add(f"**Заключение:** {conclusion}")
        # Fallback for flat / alternative structure
        if not proof:
            wf = pump_ev.get("word_family", pump_ev.get("word_description", ""))
            if wf:
                _add(f"**Семейство слов:** {wf}")
                _add()
            arg = pump_ev.get("argument", "")
            if arg:
                _add(f"**Аргумент:** {arg}")
                _add()
            steps = (pump_ev.get("formal_steps")
                     or pump_ev.get("steps")
                     or pump_ev.get("proof_steps")
                     or [])
            if steps:
                _add("**Шаги доказательства:**")
                for i, step in enumerate(steps, 1):
                    _add(f"{i}. {step}")
                _add()
            conc = pump_ev.get("conclusion", "")
            if conc:
                _add(f"**Заключение:** {conc}")
        _add()

    # Nerode
    ner = evidence.get("nerode")
    if ner:
        ner_ev = ner.get("evidence", ner)
        proof = ner_ev.get("proof") or {}
        _add("### Теорема Майхилла-Нероуда")
        if ner.get("status") == "failure" or ner_ev.get("status") == "failure":
            err = ner_ev.get("errors") or ner.get("errors") or []
            if isinstance(err, list):
                err = "; ".join(str(e) for e in err)
            _add(f"*{err}*")
            _add()
            proof = {}  # skip proof parsing
        if proof:
            ws = proof.get("word_sequence", {})
            contexts = proof.get("distinguishing_contexts", [])
            argument = proof.get("argument", "")
            conclusion = proof.get("conclusion", "")
            if ws:
                family = ws.get("family", "")
                param = ws.get("parameter", "")
                domain = ws.get("domain", "")
                _add(f"**Семейство слов:** {family} для {param} \\u2208 {domain}")
                _add()
            if contexts:
                _add("**Различающие контексты:**")
                _add("| Пара | Контекст | В языке | Не в языке |")
                _add("|------|---------|---------|------------|")
                for ctx in contexts:
                    if isinstance(ctx, dict):
                        pair = ctx.get("pair", "")
                        suffix = ctx.get("context", ctx.get("suffix", ""))
                        in_lang = ctx.get("in_language", "")
                        not_in = ctx.get("not_in_language", "")
                        _add(f"| {pair} | {suffix} | {in_lang} | {not_in} |")
                _add()
            if argument:
                _add(f"**Аргумент:** {argument}")
                _add()
            if conclusion:
                _add(f"**Заключение:** {conclusion}")
        else:
            _add(ner_ev.get("argument", str(ner)))
        _add()

    # Closure
    clo = evidence.get("closure")
    if clo:
        clo_ev = clo.get("evidence", clo)
        _add("### Замыкание")
        if clo.get("status") == "failure" or clo_ev.get("status") == "failure":
            err = clo_ev.get("errors") or clo.get("errors") or []
            if isinstance(err, list):
                err = "; ".join(str(e) for e in err)
            _add(f"*{err}*")
            _add()
            clo = None  # skip details
    if clo:
        clo_ev = clo.get("evidence", clo)
        method = clo.get("method", clo_ev.get("method", ""))
        conclusion = clo.get("conclusion", clo_ev.get("conclusion", ""))
        details = clo.get("details", clo_ev.get("details", {}))
        _add(f"**Метод:** {method}")
        if details:
            reg = details.get("regular_language", {})
            inter = details.get("intersection_result", {})
            if reg:
                regex = reg.get("regex", "")
                justification = reg.get("justification", "")
                _add(f"**Регулярный язык:** `{regex}` ({justification})")
            if inter:
                lang = inter.get("language", "")
                _add(f"**Результат пересечения:** {lang}")
        if conclusion:
            _add(f"**Заключение:** {conclusion}")
        # Fallback for flat structure
        if not details and not conclusion:
            arg = clo_ev.get("argument", "")
            if arg:
                _add(arg)
        _add()

    # --- Консолидированное доказательство ---
    reasoning = evidence.get("reasoning")
    if reasoning:
        reas_ev = reasoning.get("evidence", reasoning)
        consolidated = reas_ev.get("consolidated_proof", reasoning.get("consolidated_proof", ""))
        if consolidated:
            _add("## Консолидированное доказательство")
            _add(consolidated)
            _add()

    # --- Подсказки и наблюдения ---
    if reasoning:
        reas_ev = reasoning.get("evidence", reasoning)
        hints = reas_ev.get("hints_for_human", reasoning.get("hints_for_human", []))
        if hints:
            _add("## Подсказки и наблюдения")
            for hint in hints:
                _add(f"- {hint}")
            _add()

    # --- Верификация ---
    has_verif = evidence.get("oracle_test") or evidence.get("formalization")
    if has_verif:
        _add("## Верификация")
        _add()

    ot = evidence.get("oracle_test")
    if ot:
        _add("### Oracle Test")
        status = ot.get("status", "unknown")
        tested = ot.get("tested", "N/A")
        _add(f"Результат: {status}, протестировано слов: {tested}")
        ce = ot.get("counterexample")
        if ce:
            _add(f"Контрпример: `{ce}`")
        _add()

    fm = evidence.get("formalization")
    if fm:
        _add("### Формализация (Lean 4)")
        _add(f"Статус: {fm.get('status', 'skipped')}")
        _add()

    # --- Errors ---
    errors = result.get("errors")
    if errors:
        _add("## Ошибки")
        for err in errors:
            _add(f"- {err}")
        _add()

    # --- Итог ---
    _add("## Итог")
    _add(f"**Статус:** {result.get('status', 'unknown')}  ")
    _add(f"**Уверенность:** {result.get('confidence', 'N/A')}")
    _add()

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>TFL — Результат анализа</title>
<style>
body{font-family:'Segoe UI',system-ui,sans-serif;max-width:700px;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#1a1a1a;background:#fff}
.s-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid #e0e0e0}
.s-task{font-size:15px;line-height:1.6}
.s-label-sm{font-size:12px;color:#888;margin-bottom:6px}
.s-badge{font-size:12px;font-weight:600;padding:4px 14px;border-radius:6px;white-space:nowrap;flex-shrink:0}
.s-reg{background:#e8f5e9;color:#2e7d32}
.s-nonreg{background:#fce4ec;color:#c62828}
.s-unknown{background:#fff3e0;color:#e65100}
.s-sec{font-size:16px;font-weight:600;margin:24px 0 10px;padding-top:16px;border-top:1px solid #eee}
.s-sec:first-of-type{border-top:none;padding-top:0}
.s-p{font-size:14px;line-height:1.75;margin:6px 0}
.s-math{font-family:Georgia,'Times New Roman',serif;font-style:italic}
.s-step{display:flex;gap:10px;margin:10px 0}
.s-n{min-width:22px;height:22px;border-radius:50%;background:#f5f5f5;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#666;flex-shrink:0;margin-top:2px}
.s-t{font-size:14px;line-height:1.7}
.s-box{background:#f8f9fa;border-radius:6px;padding:12px 16px;margin:10px 0;font-size:13px;line-height:1.7}
.s-mono{font-family:Consolas,'Courier New',monospace;font-size:13px}
.s-tab{display:flex;gap:2px;margin:16px 0 12px}
.s-tab-btn{font-size:12px;padding:6px 14px;border-radius:6px 6px 0 0;background:#f5f5f5;color:#666;cursor:pointer;border:none;border-bottom:2px solid transparent;font-family:inherit}
.s-tab-btn:hover{background:#eee}
.s-tab-btn.active{background:#fff;color:#1a1a1a;border-bottom-color:#1976d2}
.s-panel{display:none}.s-panel.active{display:block}
.s-footer{display:flex;gap:16px;align-items:center;margin-top:20px;padding-top:14px;border-top:1px solid #eee;font-size:12px;color:#888;flex-wrap:wrap}
.s-dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.s-pass{background:#4caf50}.s-fail{background:#e53935}
.s-pill{font-size:11px;padding:2px 8px;border-radius:6px;background:#e3f2fd;color:#1565c0}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
th,td{border:1px solid #e0e0e0;padding:6px 10px;text-align:left}
th{background:#f5f5f5;font-weight:600}
</style>
</head>
<body>
"""

_HTML_TAIL = """\
<script>
function showTab(n){
  document.querySelectorAll('.s-tab-btn').forEach((b,i)=>b.classList.toggle('active',i===n));
  document.querySelectorAll('.s-panel').forEach((p,i)=>p.classList.toggle('active',i===n));
}
</script>
</body>
</html>"""


def _step(n: int, text: str) -> str:
    return f'<div class="s-step"><div class="s-n">{n}</div><div class="s-t">{text}</div></div>'


def _extract_steps_from_proof(proof: dict, pump_ev: dict) -> list[str]:
    """Extract numbered proof steps from pumping proof structure."""
    steps: list[str] = []
    wc = proof.get("word_choice", {})
    cut = proof.get("cut_analysis", {})

    if wc.get("word"):
        steps.append(f'Предположим L регулярен с длиной накачки <span class="s-math">p</span>.')
        steps.append(f'Выберем слово <span class="s-math">w</span> = {_esc(wc["word"])} ∈ L, '
                     f'|<span class="s-math">w</span>| ≥ <span class="s-math">p</span>.')
    if cut.get("argument"):
        steps.append(_esc(cut["argument"]))
    for case in cut.get("cases", []):
        if isinstance(case, dict):
            pumped = case.get("pumped_word", "")
            contradiction = case.get("contradiction", "")
            text = ""
            if pumped:
                text += f'Накачиваем: <span class="s-mono">{_esc(pumped)}</span>. '
            if contradiction:
                text += _esc(contradiction)
            if text:
                steps.append(text)
    conclusion = proof.get("conclusion", "")
    if conclusion:
        steps.append(_esc(conclusion) + " ∎")

    # Fallback: use flat steps
    if not steps:
        for s in (pump_ev.get("formal_steps")
                  or pump_ev.get("steps")
                  or pump_ev.get("proof_steps") or []):
            steps.append(_esc(str(s)))

    return steps


def render_html(result: dict) -> str:
    """Convert pipeline result to exam-style HTML with tabbed proofs."""
    evidence: dict = result.get("evidence") or {}
    parts: list[str] = [_HTML_TEMPLATE]
    p = parts.append

    # --- Determine final verdict ---
    reasoning = evidence.get("reasoning")
    reas_ev = (reasoning or {}).get("evidence", reasoning or {})
    final_verdict = (reas_ev.get("verdict")
                     or (reasoning or {}).get("verdict")
                     or evidence.get("hypothesis", {}).get("hypothesis", "unknown"))
    final_confidence = result.get("confidence", 0.0)

    # Badge
    if final_verdict == "regular":
        badge_cls, badge_text = "s-reg", "регулярен"
    elif final_verdict == "non_regular":
        badge_cls, badge_text = "s-nonreg", "нерегулярен"
    else:
        badge_cls, badge_text = "s-unknown", "не определено"

    # --- Source text ---
    source_text = ""
    # Try to get source_text from the IR stored in various places
    for key in ("classifier", "pumping", "nerode", "closure"):
        agent = evidence.get(key, {})
        agent_ev = agent.get("evidence", agent)
        if isinstance(agent_ev, dict):
            ir_inner = agent_ev.get("ir", {})
            if isinstance(ir_inner, dict) and ir_inner.get("source_text"):
                source_text = ir_inner["source_text"]
                break
    # Fallback to hypothesis-level
    if not source_text:
        hyp = evidence.get("hypothesis", {})
        source_text = hyp.get("source_text", "")

    # --- Header ---
    p('<div class="s-head">')
    p('  <div>')
    p('    <div class="s-label-sm">Задача (экзамен ИУ-9)</div>')
    p(f'    <div class="s-task">{_esc(source_text) if source_text else "—"}</div>')
    p('  </div>')
    p(f'  <span class="s-badge {badge_cls}">{badge_text}</span>')
    p('</div>')

    # --- Key fact / observation ---
    # Extract from classifier or consolidated proof
    clf = evidence.get("classifier", {})
    clf_ev = clf.get("evidence", clf)
    observation = clf_ev.get("reasoning", clf.get("reasoning", ""))

    if observation:
        p('<div class="s-sec" style="border-top:none;padding-top:0">Наблюдение</div>')
        p(f'<div class="s-p">{_esc(observation)}</div>')

    # --- Consolidated proof as key fact box ---
    consolidated = reas_ev.get("consolidated_proof", (reasoning or {}).get("consolidated_proof", ""))

    # --- Proof tabs ---
    proof_tabs: list[tuple[str, str]] = []  # (label, panel_html)

    # Pumping
    pump = evidence.get("pumping")
    if pump:
        pump_ev = pump.get("evidence", pump)
        proof = pump_ev.get("proof", {})
        steps = _extract_steps_from_proof(proof, pump_ev)
        panel = ""
        for i, s in enumerate(steps, 1):
            panel += _step(i, s)
        proof_tabs.append(("Лемма о накачке", panel))

    # Nerode
    ner = evidence.get("nerode")
    if ner:
        ner_ev = ner.get("evidence", ner)
        proof = ner_ev.get("proof") or {}
        panel = ""
        step_n = 1
        # Skip if agent failed
        if ner.get("status") == "failure" or ner_ev.get("status") == "failure":
            err_msg = (ner_ev.get("errors") or ner.get("errors") or "Агент не смог построить доказательство")
            panel = f'<div class="s-p" style="color:#888">{_esc(err_msg)}</div>'
            proof_tabs.append(("Майхилл-Нероуд", panel))
            ner = None  # skip further processing
        if ner:
            ws = proof.get("word_sequence") or {}
            if ws:
                family = ws.get("family", "")
                examples = ws.get("examples", [])
                ex_str = ", ".join(examples[:4]) + ", ..." if examples else ""
                panel += _step(step_n, f'Рассмотрим бесконечное семейство слов: '
                                       f'{_esc(family)} ({_esc(ex_str)})')
                step_n += 1
            contexts = proof.get("distinguishing_contexts", [])
            if contexts:
                ctx = contexts[0] if contexts else {}
                context_val = ctx.get("context", "")
                in_lang = ctx.get("in_language", "")
                not_in = ctx.get("not_in_language", "")
                panel += _step(step_n,
                    f'Для различных <span class="s-math">i, j</span> слова различимы '
                    f'контекстом <span class="s-math">z</span> = {_esc(context_val)}:')
                step_n += 1
                panel += '<div class="s-box">'
                if in_lang:
                    panel += f'{_esc(in_lang)}<br>'
                if not_in:
                    panel += f'{_esc(not_in)}'
                panel += '</div>'
            argument = proof.get("argument", ner_ev.get("argument", ""))
            conclusion = proof.get("conclusion", ner_ev.get("conclusion", ""))
            if conclusion:
                panel += _step(step_n, f'{_esc(conclusion)} ∎')
            elif argument:
                panel += _step(step_n, f'{_esc(argument)} ∎')
            proof_tabs.append(("Майхилл-Нероуд", panel))

    # Closure
    clo = evidence.get("closure")
    if clo:
        clo_ev = clo.get("evidence", clo)
        details = clo.get("details", clo_ev.get("details", {}))
        conclusion = clo.get("conclusion", clo_ev.get("conclusion", ""))
        panel = ""
        step_n = 1
        reg = details.get("regular_language", {})
        if reg:
            panel += _step(step_n, f'Язык R = <span class="s-mono">{_esc(reg.get("regex", "a*b*"))}</span> регулярен.')
            step_n += 1
        inter = details.get("intersection_result", {})
        if inter:
            lang = inter.get("language", "")
            panel += _step(step_n, f'L ∩ R = {_esc(lang)}')
            step_n += 1
            is_reg = inter.get("is_regular", True)
            if not is_reg:
                proof_method = inter.get("proof_method", "")
                panel += _step(step_n, f'Этот язык нерегулярен ({_esc(proof_method)}).')
                step_n += 1
        if conclusion:
            panel += _step(step_n, f'{_esc(conclusion)} ∎')
        else:
            arg = clo_ev.get("argument", "")
            if arg:
                panel += _step(step_n, f'{_esc(arg)} ∎')
        proof_tabs.append(("Замыкание", panel))

    # RE Builder
    reb = evidence.get("re_builder")
    if reb:
        reb_ev = reb.get("evidence", reb)
        regex = reb_ev.get("regex", "")
        explanation = reb_ev.get("explanation", "")
        panel = _step(1, f'Регулярное выражение: <span class="s-mono">{_esc(regex)}</span>')
        if explanation:
            panel += _step(2, _esc(explanation))
        proof_tabs.append(("Рег. выражение", panel))

    # DFA
    dfa_out = evidence.get("dfa_builder")
    if dfa_out:
        dfab_ev = dfa_out.get("evidence", dfa_out)
        dfa_obj = dfab_ev.get("dfa")
        explanation = dfab_ev.get("explanation", "")
        panel = ""
        if explanation:
            panel += f'<div class="s-p">{_esc(explanation)}</div>'
        if dfa_obj:
            panel += _dfa_table_html(dfa_obj)
        proof_tabs.append(("ДКА", panel))

    # Render tabs
    if proof_tabs:
        p('<div class="s-tab">')
        for i, (label, _) in enumerate(proof_tabs):
            active = " active" if i == 0 else ""
            p(f'  <button class="s-tab-btn{active}" onclick="showTab({i})">{_esc(label)}</button>')
        p('</div>')

        for i, (_, panel_html) in enumerate(proof_tabs):
            active = " active" if i == 0 else ""
            p(f'<div class="s-panel{active}" id="panel-{i}">')
            p(panel_html)
            p('</div>')

    # --- Hints for human ---
    if reasoning:
        r_ev = (reasoning or {}).get("evidence", reasoning or {})
        hints = r_ev.get("hints_for_human", (reasoning or {}).get("hints_for_human", []))
        if hints:
            p('<div class="s-sec">Подсказки и наблюдения</div>')
            for hint in hints:
                p(f'<div class="s-step"><div class="s-n">💡</div>'
                  f'<div class="s-t">{_esc(hint)}</div></div>')

    # --- Footer ---
    footer_items: list[str] = []
    ot = evidence.get("oracle_test")
    if ot:
        status = ot.get("status", "unknown")
        tested = ot.get("tested", "?")
        dot_cls = "s-pass" if status == "pass" else "s-fail"
        footer_items.append(f'<span style="display:flex;align-items:center;gap:5px">'
                            f'<span class="s-dot {dot_cls}"></span> '
                            f'oracle: {_esc(tested)}/{_esc(tested)}</span>')

    footer_items.append(f'<span>confidence: {final_confidence}</span>')

    # Lean 4 verification status
    fm = evidence.get("formalization", {})
    if fm:
        fm_status = fm.get("status", "skipped")
        sorry_count = fm.get("sorry_count", 0)
        if fm_status == "valid" and sorry_count == 0:
            footer_items.append(
                '<span style="display:flex;align-items:center;gap:5px">'
                '<span class="s-dot s-pass"></span> Lean 4 verified</span>')
        elif fm_status == "valid" and sorry_count > 0:
            footer_items.append(
                f'<span style="color:#e65100">Lean 4: {sorry_count} sorry</span>')
        elif fm_status == "skipped":
            footer_items.append(
                f'<span style="color:#888">Lean 4: not checked</span>')
        elif fm_status == "invalid":
            footer_items.append(
                '<span style="display:flex;align-items:center;gap:5px">'
                '<span class="s-dot s-fail"></span> Lean 4: errors</span>')

    # Errors
    errors = result.get("errors")
    if errors:
        p('<div class="s-sec">Ошибки</div>')
        for err in errors:
            p(f'<div class="s-p" style="color:#c62828">⚠ {_esc(err)}</div>')

    # Count agreed methods
    proof_agents = [k for k in ("pumping", "nerode", "closure") if evidence.get(k)]
    if len(proof_agents) > 1:
        footer_items.append(f'<span class="s-pill">{len(proof_agents)} метода согласованы</span>')

    p('<div class="s-footer">')
    for item in footer_items:
        p(f'  {item}')
    p('</div>')

    p(_HTML_TAIL)
    return "\n".join(parts)


def _dfa_table_html(dfa: dict) -> str:
    """Render DFA transition table as an HTML table."""
    states = dfa.get("states", [])
    start = dfa.get("start")
    accept = set(dfa.get("accept", []))
    alphabet = sorted(dfa.get("alphabet", []))
    transitions = dfa.get("transitions", {})

    rows = ['<table><tr><th>State</th>']
    for sym in alphabet:
        rows.append(f"<th>{_esc(sym)}</th>")
    rows.append("<th>Accept</th></tr>")

    for st in states:
        name = f"\u2192{_esc(st)}" if st == start else _esc(str(st))
        rows.append(f"<tr><td>{name}</td>")
        for sym in alphabet:
            nxt = None
            if isinstance(transitions, dict):
                nxt = transitions.get((st, sym))
                if nxt is None and isinstance(transitions.get(st), dict):
                    nxt = transitions[st].get(sym)
            rows.append(f"<td>{_esc(nxt) if nxt is not None else ''}</td>")
        rows.append(f"<td>{'\u2713' if st in accept else ''}</td></tr>")

    rows.append("</table>")
    return "".join(rows)


# ---------------------------------------------------------------------------
# render_to_file
# ---------------------------------------------------------------------------

def render_to_file(result: dict, path: str, fmt: str = "md") -> None:
    """Write rendered output to a file.

    Parameters
    ----------
    result : dict
        Pipeline result dict.
    path : str
        Output file path.
    fmt : str
        ``"md"`` for Markdown (default) or ``"html"`` for HTML.
    """
    if fmt == "html":
        content = render_html(result)
    else:
        content = render_markdown(result)
    Path(path).write_text(content, encoding="utf-8")
