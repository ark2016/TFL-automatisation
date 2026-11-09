//===========================================================================
// Deep Agent tab of TFL Lab: tfl_deep_agent through /api/deep/* (ui_server/deep_routes.py)
//
// A chat: the runs on the left, the conversation of one run in the middle (the
// task, the agent's activity folded into short rows, the plan, the review
// stops as small cards, the final card), the report document on the right.
// Loaded after app.js and uses its helpers ($, el, api, sendJson, num, fmtUsd,
// toMs, fmtDur, fmtWhen, ensureMdLibs, preprocessObsidianCallouts).
// Trust labels and evidence come from code (the run's ledger); this tab only
// shows them.
//===========================================================================

// ---- deep: pure helpers begin (no DOM; ui_server/tests/test_deep_frontend.py runs them under node) ----
const DEEP_TRUST_LEVELS = ['refuted', 'verified', 'bounded_pass', 'well_formed', 'not_verified'];
const DEEP_TRUST_LABEL = {
  refuted: 'refuted', verified: 'verified', bounded_pass: 'bounded pass',
  well_formed: 'well-formed', not_verified: 'not verified',
};
const DEEP_ACTIVE = new Set(['queued', 'running']);
const DEEP_CLASSES = ['reg', 'cfl', 'dcfl', 'll'];
const DEEP_TEMPLATES_FALLBACK = ['full_proof', 'exam_solution', 'short_verdict', 'lean_appendix'];
const DEEP_REVIEW_MODES = [
  ['all', 'Подтверждать всё'],
  ['formalization', 'Только формализацию'],
  ['auto', 'Без проверок'],
];
// a review point: its name in a decision line and the past forms of the decisions
const DEEP_POINT = {
  formalization: {title: 'Формализация', name: 'формализация', approved: 'принята', edited: 'исправлена', rejected: 'отклонена'},
  report: {title: 'Черновик отчёта', name: 'отчёт', approved: 'принят', edited: 'исправлен', rejected: 'отклонён'},
  execute: {title: 'Команда в песочнице', name: 'команда', approved: 'разрешена', edited: 'исправлена', rejected: 'отклонена'},
  budget: {title: 'Бюджет задачи заканчивается', name: 'бюджет'},
};
const DEEP_STATUS_RU = {
  queued: 'в очереди', running: 'работает', awaiting_human: 'ждёт решения', completed: 'готово',
  error: 'ошибка', cancelled: 'остановлен', interrupted: 'прерван',
};
// submit_report fields a reviewer may edit at the report stop (tools/report.py ReportInput)
const DEEP_REPORT_FIELDS = ['verdict', 'answer', 'language_class', 'method', 'summary', 'hypothesis',
  'construction', 'conclusion', 'template'];

function deepTrustClass(t) { return 'trust ' + (DEEP_TRUST_LEVELS.includes(t) ? 'trust-' + t : 'trust-not_verified'); }
function deepTrustLabel(t) { return DEEP_TRUST_LABEL[t] || (t ? String(t) : 'not verified'); }
function deepIsActive(st) { return DEEP_ACTIVE.has(st); }
function deepWord(w) { return w === '' ? 'ε' : String(w); }
// JSON with sorted keys: equality of two JSON values regardless of key order.
function deepCanon(v) {
  if (Array.isArray(v)) return '[' + v.map(deepCanon).join(',') + ']';
  if (v && typeof v === 'object') {
    return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + deepCanon(v[k])).join(',') + '}';
  }
  return JSON.stringify(v === undefined ? null : v);
}
function deepSame(a, b) { return deepCanon(a) === deepCanon(b); }
function deepShortModel(m) {
  const s = String(m || '');
  const t = s.match(/^claude-(haiku|sonnet|opus)\b/);
  return t ? t[1] : s;
}
function deepModelName(m) {
  if (m && typeof m === 'object') return String(m.effective_model || m.model || m.tier || m.requested || '');
  return m ? String(m) : '';
}
function deepTimeOf(ts) {
  const ms = toMs(ts);
  if (ms === null) return '';
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}
// Russian plural: deepPlural(4, ['инструмент', 'инструмента', 'инструментов']) -> 'инструмента'
function deepPlural(n, forms) {
  const a = Math.abs(Math.trunc(Number(n) || 0));
  const d = a % 10, h = a % 100;
  if (d === 1 && h !== 11) return forms[0];
  if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return forms[1];
  return forms[2];
}
// $ for limits and the day line: $20, $1.50, $0.01, $0.0040, $0
function deepUsdShort(v) {
  const n = num(v);
  if (n === null) return '—';
  if (n === 0) return '$0';
  if (n >= 1) return '$' + (Number.isInteger(n) ? String(n) : n.toFixed(2));
  return '$' + n.toFixed(n < 0.01 ? 4 : 2);
}
function deepClip(s, n) {
  const t = String(s ?? '').replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n - 1).trimEnd() + '…' : t;
}
// The title of a run in the list: the first words of its task.
function deepTitleFromText(text, maxChars) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  if (!t) return '';
  const lim = maxChars || 46;
  if (t.length <= lim) return t;
  const cut = t.slice(0, lim);
  const sp = cut.lastIndexOf(' ');
  return (sp > lim * 0.5 ? cut.slice(0, sp) : cut).replace(/[\s,.;:—-]+$/, '') + '…';
}

// The kind of a chosen / dropped file: {kind: 'file'|'text', mime, label} or null.
function deepFileKind(name, type) {
  const ext = String(name || '').toLowerCase().split('.').pop();
  const t = String(type || '').toLowerCase();
  if (t === 'application/pdf' || ext === 'pdf') return {kind: 'file', mime: 'application/pdf', label: 'PDF'};
  if (t === 'image/png' || ext === 'png') return {kind: 'file', mime: 'image/png', label: 'PNG'};
  if (t === 'image/jpeg' || ext === 'jpg' || ext === 'jpeg') return {kind: 'file', mime: 'image/jpeg', label: 'JPG'};
  if (t === 'image/webp' || ext === 'webp') return {kind: 'file', mime: 'image/webp', label: 'WEBP'};
  if (t.startsWith('text/') || ext === 'txt' || ext === 'md') return {kind: 'text', mime: 'text/plain', label: 'TXT'};
  return null;
}
// Why a file cannot be sent (null = it can); the server checks again (intake).
function deepFileProblem(name, type, size, maxMb) {
  const k = deepFileKind(name, type);
  if (!k) return `файл ${name || ''} (${type || 'тип неизвестен'}) не поддерживается: нужен PDF, PNG, JPG, WebP или текст`;
  const lim = num(maxMb);
  const n = num(size);
  if (k.kind === 'file' && lim !== null && n !== null && n > lim * 1024 * 1024) {
    return `файл ${(n / 1048576).toFixed(1)} MB, лимит ${lim} MB (intake.max_file_mb)`;
  }
  return null;
}
function deepB64FromDataUrl(s) {
  const t = String(s || '');
  const i = t.indexOf(',');
  return t.startsWith('data:') && i >= 0 ? t.slice(i + 1) : t;
}

// The review mode the saved settings mean (hitl.formalization / hitl.report).
function deepReviewModeOf(settings) {
  const h = (settings && settings.hitl) || {};
  const f = h.formalization !== false, r = h.report !== false;
  if (f && r) return 'all';
  if (f) return 'formalization';
  return r ? 'all' : 'auto';
}
// settings_override for one run: only what differs from the saved settings.
function deepOverride(form, settings) {
  const s = settings || {};
  const out = {};
  const budget = num(form.perTaskUsd);
  if (budget !== null && budget !== num(s.budget && s.budget.per_task_usd)) out.budget = {per_task_usd: budget};
  const roles = (s.models && s.models.roles) || {};
  if (form.mainModel && form.mainModel !== roles.main) out.models = {roles: {main: form.mainModel}};
  const hitl = s.hitl || {};
  const h = {};
  if (typeof form.hitlFormalization === 'boolean' && form.hitlFormalization !== !!hitl.formalization) h.formalization = form.hitlFormalization;
  if (typeof form.hitlReport === 'boolean' && form.hitlReport !== !!hitl.report) h.report = form.hitlReport;
  if (Object.keys(h).length) out.hitl = h;
  return Object.keys(out).length ? out : null;
}

// The body of POST /api/deep/runs, or {error}. form.mode: 'mock' | 'inline' | 'live'.
// form.reviewMode ('all' | 'formalization' | 'auto') goes as review_mode; with form.legacy
// (a server without review_mode) it is already folded into override.hitl / autoApprove.
function deepCreateBody(form) {
  const body = {};
  if (form.file) {
    if (!form.file.b64) return {error: 'файл ещё читается'};
    body.file_b64 = form.file.b64;
    body.mime = form.file.mime;
    if (form.file.name) body.name = form.file.name;
  } else {
    const text = String(form.text || '').trim();
    if (!text) return {error: 'опишите задачу или прикрепите файл'};
    body.text = text;
  }
  if (form.template) body.template = form.template;
  if (form.override) body.settings_override = form.override;
  const live = form.mode === 'live';
  const rm = form.reviewMode || null;
  if (rm && !DEEP_REVIEW_MODES.some(([v]) => v === rm)) return {error: 'неизвестный режим подтверждений ' + rm};
  if (rm && !form.legacy) body.review_mode = rm;
  if (form.autoApprove && !live) body.auto_approve = true;
  if (live) {
    // a live run may choose review_mode "auto" explicitly; the older auto_approve flag stays mock-only
    if (form.autoApprove) return {error: 'auto_approve — только для mock: в live выберите режим «Без проверок» явно'};
    const h = (form.override && form.override.hitl) || {};
    const off = Object.keys(h).filter(k => h[k] === false);
    if (off.length) return {error: `платный запуск сохраняет точки проверки (${off.join(', ')}): отключают их в настройках, не для одного запуска`};
    const cap = num(form.override && form.override.budget && form.override.budget.per_task_usd);
    const saved = num(form.savedPerTask);
    if (cap !== null && saved !== null && cap > saved) return {error: `в live-запуске бюджет можно только уменьшить (в настройках ${deepUsdShort(saved)})`};
    if (form.confirm !== true) return {error: 'live-запуск тратит деньги на API: сначала отметьте «подтверждаю расходы»'};
    body.live = true;
    body.confirm_spend = true;
  } else if (form.mode === 'inline') {
    let script;
    try { script = JSON.parse(String(form.mockInline || '')); }
    catch (e) { return {error: 'сценарий mock — не JSON: ' + e.message}; }
    if (!script || typeof script !== 'object' || Array.isArray(script) || !script.models
        || typeof script.models !== 'object' || !Object.keys(script.models).length) {
      return {error: 'сценарию mock нужен непустой объект "models" {role: [replies]}'};
    }
    body.mock_script = script;
  } else {
    if (!form.mockName) return {error: 'выберите сценарий mock: без него mock-запуск невозможен'};
    body.mock_script = form.mockName;
  }
  return {body};
}

// Formalization edit: the changed fields of {transcription, ir, class}, or {error}.
function deepHitl1Edits(args, form) {
  const a = args || {};
  const out = {};
  if (form.transcription !== undefined) {
    const t = String(form.transcription).trim();
    const orig = String(a.transcription || a.task_text || '').trim();
    if (t !== orig) {
      if (!t) return {error: 'транскрипция пуста'};
      out.transcription = t;
    }
  }
  if (form.irText !== undefined && String(form.irText).trim()) {
    let ir;
    try { ir = JSON.parse(String(form.irText)); }
    catch (e) { return {error: 'IR — не JSON: ' + e.message}; }
    if (!ir || typeof ir !== 'object' || Array.isArray(ir)) return {error: 'IR должен быть JSON-объектом'};
    if (!deepSame(ir, a.ir)) out.ir = ir;
  }
  if (form.cls && form.cls !== a.class) {
    if (!('ir' in out)) return {error: `смена класса на ${form.cls} требует IR этого класса: исправьте и IR`};
    out.class = form.cls;
  }
  return {payload: out, changed: Object.keys(out)};
}

// Report edit: the changed report fields, or {error}. form.stepsText is the steps as JSON text.
function deepReportEdits(args, form) {
  const a = args || {};
  const out = {};
  for (const k of DEEP_REPORT_FIELDS) {
    if (form[k] === undefined) continue;
    if (k === 'answer') {
      const v = form[k] === '' || form[k] === null ? null : String(form[k]);
      if ((a.answer ?? null) !== v) out.answer = v;
      continue;
    }
    const v = String(form[k]);
    const orig = k === 'template' ? (a.template || 'full_proof') : String(a[k] ?? '');
    if (v !== orig) out[k] = v;
  }
  for (const k of ['verdict', 'method']) {
    if (out[k] !== undefined && !out[k].trim()) return {error: `поле ${k} не может быть пустым`};
  }
  if (form.stepsText !== undefined) {
    let steps;
    try { steps = JSON.parse(String(form.stepsText)); }
    catch (e) { return {error: 'шаги — не JSON: ' + e.message}; }
    if (!Array.isArray(steps) || !steps.length) return {error: 'шаги должны быть непустым JSON-массивом'};
    if (!deepSame(steps, a.steps || [])) out.steps = steps;
  }
  return {payload: out, changed: Object.keys(out)};
}

// Any other stop (sandbox execute ...): the changed keys of the args object.
function deepArgsEdits(args, text) {
  let obj;
  try { obj = JSON.parse(String(text || '')); }
  catch (e) { return {error: 'аргументы — не JSON: ' + e.message}; }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return {error: 'аргументы должны быть JSON-объектом'};
  const a = args || {};
  const out = {};
  for (const k of Object.keys(obj)) if (!deepSame(obj[k], a[k])) out[k] = obj[k];
  return {payload: out, changed: Object.keys(out)};
}

// The body of POST /api/deep/runs/<id>/resume, or {error}.
function deepResumeBody(decision, opts) {
  const o = opts || {};
  if (!['approve', 'edit', 'reject'].includes(decision)) return {error: 'неизвестное решение ' + decision};
  if (Array.isArray(o.allowed) && o.allowed.length && !o.allowed.includes(decision)) {
    return {error: `здесь нельзя «${decision}» (можно: ${o.allowed.join(', ')})`};
  }
  const body = {decision};
  const comment = String(o.comment || '').trim();
  if (comment) body.comment = comment;
  if (decision === 'edit') {
    if (!o.payload || typeof o.payload !== 'object' || !Object.keys(o.payload).length) {
      return {error: 'ничего не изменено: исправьте поле или примите как есть'};
    }
    body.payload = o.payload;
  } else if (decision === 'reject' && !comment) {
    return {error: 'для отклонения нужен комментарий: агент продолжит с ним'};
  }
  if (o.approveRest) {
    if (decision !== 'approve') return {error: '«принять всё до конца» — только вместе с «принять»'};
    body.approve_rest = true;
  }
  if (o.live) {
    if (o.confirm !== true) return {error: 'live-запуск: продолжение тратит деньги на API, отметьте «подтверждаю расходы»'};
    body.confirm_spend = true;
  }
  return {body};
}

// Every tool call of a pending stop (`actions` of the detail view; an older
// response carries only the first one as action / args).
function deepPendingActions(p) {
  if (!p) return [];
  if (Array.isArray(p.actions) && p.actions.length) return p.actions.filter(a => a && typeof a === 'object');
  if (p.action || p.args) return [{name: p.action, args: p.args, description: p.description, allowed_decisions: p.allowed_decisions}];
  return [];
}

// The template the report was rendered with: result.json `rendered.template` (an edit
// at the report stop may change it), else the run's template that the detail view reports.
function deepReportTemplate(res, j) {
  const r = j && typeof j === 'object' ? j : {};
  return (r.rendered && r.rendered.template) || (r.report && r.report.template) || (res && res.template) || '';
}

// Short summary lines of a pending stop when the server sends no pending_point (older servers).
function deepFallbackSummary(point, raw) {
  const p = raw || {};
  const out = [];
  if (point === 'formalization') {
    const a = (deepPendingActions(p)[0] || {}).args || p.args || {};
    const ir = a.ir && typeof a.ir === 'object' ? a.ir : {};
    const spec = ir.language_spec && typeof ir.language_spec === 'object' ? ir.language_spec : {};
    const what = spec.pattern || spec.regex || spec.description || ir.source_text || '';
    const alpha = Array.isArray(spec.alphabet) ? spec.alphabet : (Array.isArray(ir.alphabet) ? ir.alphabet : null);
    const head = [a.class ? String(a.class).toUpperCase() : '', what ? deepClip(what, 60) + (alpha ? ` над {${alpha.join(', ')}}` : '') : '']
      .filter(Boolean).join(' · ');
    if (head) out.push(head);
    const ex = a.examples || {};
    const words = (xs) => (Array.isArray(xs) ? xs.slice(0, 4).map(deepWord).join(', ') : '');
    if (Array.isArray(ex.in_language) || Array.isArray(ex.not_in_language)) {
      out.push(`в L: ${words(ex.in_language) || '—'} · нет: ${words(ex.not_in_language) || '—'}`);
    }
    if (a.valid === false) out.push('IR не прошёл validate_ir' + (Array.isArray(a.errors) && a.errors.length ? ': ' + deepClip(a.errors[0], 80) : ''));
    else if (!head && (a.transcription || a.task_text)) out.push(deepClip(a.transcription || a.task_text, 90));
  } else if (point === 'report') {
    const d = p.draft || {};
    const a = (deepPendingActions(p)[0] || {}).args || {};
    const verdict = d.verdict || a.verdict;
    if (verdict) out.push(deepClip(verdict, 90));
    const bits = [d.trust ? deepTrustLabel(d.trust) : '', num(d.confidence_cap) !== null ? `cap ${num(d.confidence_cap).toFixed(2)}` : '',
      a.template ? 'шаблон ' + a.template : ''].filter(Boolean);
    if (bits.length) out.push(bits.join(' · '));
    if (Array.isArray(d.unknown_ids) && d.unknown_ids.length) out.push('ссылки вне журнала доказательств: ' + d.unknown_ids.join(', '));
  } else {
    deepPendingActions(p).slice(0, 3).forEach(a => {
      const args = a.args || {};
      out.push(deepClip(`${a.name || 'действие'}: ${args.command || args.cmd || JSON.stringify(args)}`, 90));
    });
  }
  return out.slice(0, 4);
}

// The pending stop of a run detail, or null:
// {point, title, summary[], detailsMd, editable[]|null, allowed[], interruptId, askedAt, legacy, raw}.
// Reads the `pending_point` object (detail or detail.run); falls back to hitl.pending.
function deepPendingView(detail) {
  const d = detail || {};
  const run = d.run || {};
  if (run.status !== 'awaiting_human') return null;
  const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : null);
  const pp = obj(d.pending_point) || obj(run.pending_point);
  const raw = obj(d.hitl && d.hitl.pending) || {};
  const point = String((pp && pp.point) || raw.point || (typeof run.pending_point === 'string' ? run.pending_point : '') || '');
  const meta = DEEP_POINT[point];
  const own = pp && Array.isArray(pp.summary)
    ? pp.summary.filter(s => s !== null && s !== undefined && String(s).trim()).slice(0, 4).map(s => String(s)) : [];
  const summary = own.length ? own : deepFallbackSummary(point, raw);
  let allowed = (pp && Array.isArray(pp.allowed_decisions) && pp.allowed_decisions.length) ? pp.allowed_decisions
    : (Array.isArray(raw.allowed_decisions) && raw.allowed_decisions.length ? raw.allowed_decisions : null);
  const editable = pp && Array.isArray(pp.editable) ? pp.editable.map(String) : null;
  if (!allowed) allowed = ['approve', 'reject'].concat(editable && editable.length ? ['edit'] : []);
  return {
    point,
    title: String((pp && pp.title) || (meta ? meta.title : 'Нужно решение')),
    summary,
    detailsMd: String((pp && (pp.details_md ?? pp.details)) || raw.card || ''),
    editable,
    allowed: allowed.slice(),
    interruptId: (pp && pp.interrupt_id) || raw.interrupt_id || '',
    askedAt: (pp && pp.asked_at) || raw.asked_at || '',
    legacy: !pp,
    raw,
  };
}

// The body of POST /api/deep/runs/<id>/message, or {error}: the text, or {preset: 'lean'}.
function deepMessageBody(text, opts) {
  const o = opts || {};
  const body = {};
  if (o.preset) {
    body.preset = o.preset;
  } else {
    const t = String(text || '').trim();
    if (!t) return {error: 'напишите сообщение'};
    body.text = t;
  }
  if (o.live) {
    if (o.confirm !== true) return {error: 'live: сообщение тратит деньги на API, отправка только кнопкой'};
    body.confirm_spend = true;
  }
  return {body};
}

// What the composer does with the run open now: {kind, live, placeholder, note}.
//   new      a new task (no run open; a server without `followup` as well)
//   message  a follow-up to this run;  busy  wait for the agent / for a decision;  closed  cannot be continued
function deepComposerState(detail, runId) {
  if (!runId) return {kind: 'new', live: false, placeholder: '', note: ''};
  const d = detail || {};
  const run = d.run;
  if (!run) return {kind: 'busy', live: false, placeholder: '', note: ''};
  const f = d.followup;
  if (!f || typeof f !== 'object') return {kind: 'new', live: false, placeholder: '', note: ''};
  const live = !!run.live;
  if (f.can_message) {
    const b = d.budget || {};
    const money = num(b.limit_usd) !== null ? ` · потрачено ${deepUsdShort(b.spent_usd ?? 0)} из ${deepUsdShort(b.limit_usd)}` : '';
    return {kind: 'message', live, placeholder: 'Спросите агента или попросите доработать…',
      note: live ? `live: сообщение платное, отправка только кнопкой${money}` : ''};
  }
  if (deepIsActive(run.status)) return {kind: 'busy', live, placeholder: 'Агент работает…', note: ''};
  if (run.status === 'awaiting_human') return {kind: 'busy', live, placeholder: 'Агент ждёт вашего решения…', note: ''};
  return {kind: 'closed', live, placeholder: 'Этот запуск не продолжить',
    note: f.reason || 'Запуск не продолжить: он остановлен или прерван. Новая задача — кнопка «Новая».'};
}

// The dollars to add at the budget stop, from a button or the number input: {add} or {error}.
function deepParseBudgetAdd(value, dayLeft) {
  const add = Number(String(value ?? '').trim().replace(',', '.'));
  if (!Number.isFinite(add) || add <= 0) return {error: 'укажите сумму больше нуля'};
  const day = num(dayLeft);
  if (day !== null && add > day + 1e-9) return {error: `на сегодня осталось только ${deepUsdShort(day)}`};
  return {add};
}

// A decision source that says the run decided itself ("auto", "auto-approve", ...).
function deepIsAutoSource(source) {
  return /auto/.test(String(source || ''));
}

// A decision line: "✓ формализация принята · вы", with the edited fields / the comment.
function deepDecisionText(x) {
  if (x.point === 'budget') {
    const who = deepIsAutoSource(x.source) ? 'автоматически' : 'вы';
    if (x.decision === 'extended') {
      return {text: `✓ бюджет: +${deepUsdShort(x.add_usd)}, лимит ${deepUsdShort(x.new_limit_usd)} · ${who}`, kind: 'edited'};
    }
    return {text: `✓ бюджет: завершить с тем, что есть · ${who}`, kind: 'approved'};
  }
  const meta = DEEP_POINT[x.point] || {name: String(x.point || 'решение'), approved: 'принято', edited: 'исправлено', rejected: 'отклонено'};
  const dec = String(x.decision || '');
  const kind = /^auto/.test(dec) ? 'auto' : (/^edit/.test(dec) ? 'edited' : (/^reject/.test(dec) ? 'rejected' : 'approved'));
  const mark = kind === 'edited' ? '✎' : (kind === 'rejected' ? '✗' : '✓');
  const verb = kind === 'auto' ? meta.approved : meta[kind];
  const src = String(x.source || '');
  const who = /approve_rest/.test(src) ? 'вы, всё до конца'
    : (kind === 'auto' || deepIsAutoSource(src) ? 'автоматически' : (src && src !== 'human' ? src : 'вы'));
  let text = `${mark} ${meta.name} ${verb} · ${who}`;
  const edited = Array.isArray(x.edited_fields) ? x.edited_fields : [];
  if (kind === 'edited' && edited.length) text += ` (${edited.join(', ')})`;
  if (x.comment) text += ` · «${deepClip(x.comment, 120)}»`;
  return {text, kind};
}

function deepToolName(ev) {
  return ev.tool === 'task' && ev.subagent_type ? `task → ${ev.subagent_type}` : String(ev.tool || 'tool');
}

// progress.jsonl events -> chat items (tfl_deep_agent/progress.py event kinds):
//   activity  consecutive model / tool calls of the main agent, one collapsed row
//   agent     a subagent scope (subagent_start ... end), a spawned agent or a workflow
//   plan      where the agent first wrote its plan (the latest todos are shown there)
//   decision  a review decision;  note  a budget signal or an error;  done  the end of the run
//   user / assistant  a follow-up message and the agent's text answer
function deepChatItems(events) {
  const items = [];
  let act = null;
  const open = [];             // open agent items with a budget node, innermost last
  const byName = {};           // spawn / workflow name -> its item
  let planShown = false;
  let lastAgentAt = -2;        // index of the last subagent_start / spawn_start
  const newGroup = (type, key, extra) => Object.assign({type, key, tools: [], modelCalls: 0, models: [], cost: 0, errors: 0}, extra || {});
  const add = (g, ev, i) => {
    if (ev.event === 'model_call') {
      g.modelCalls++;
      g.cost += num(ev.usd) || 0;
      const m = deepShortModel(deepModelName(ev.model));
      if (m && !g.models.includes(m)) g.models.push(m);
      if (ev.outcome && ev.outcome !== 'ok') g.warn = String(ev.outcome);
      return;
    }
    const ok = ev.ok !== false && ev.status !== 'error' && ev.status !== 'exception';
    g.tools.push({i, name: deepToolName(ev), ok, trust: ev.trust || 'not_verified', reason: String(ev.trust_reason || ''),
      error: ok ? '' : [ev.error_kind, ev.error].filter(Boolean).join(': '), args: String(ev.args_preview || ''), id: ev.id || ''});
    g.cost += num(ev.cost_usd) || 0;
    if (!ok) g.errors++;
  };
  const inside = (path) => {
    for (let j = open.length - 1; j >= 0; j--) {
      const n = open[j].node;
      if (n && (path === n || path.startsWith(n + '/'))) return open[j];
    }
    return null;
  };
  const close = (a) => { const j = open.indexOf(a); if (j >= 0) open.splice(j, 1); };
  (events || []).forEach((ev, i) => {
    const k = String(ev.event || '');
    switch (k) {
      case 'model_call':
      case 'tool_call': {
        const path = String(ev.scope || ev.node || '');
        const a = inside(path);
        if (a) { add(a, ev, i); break; }
        if (k === 'tool_call' && ev.tool === 'write_todos') break;       // the plan item shows it
        if (k === 'tool_call' && ev.tool === 'task' && ev.subagent_type
            && items.some(x => x.type === 'agent' && x.name === ev.subagent_type)) break;   // the agent row shows it
        if (!act) { act = newGroup('activity', 'a' + i); items.push(act); }
        add(act, ev, i);
        break;
      }
      case 'subagent_start': {
        act = null;
        const node = String(ev.node || '');
        const prev = items[items.length - 1];
        if (lastAgentAt === i - 1 && prev && prev.type === 'agent' && prev.kind === 'spawn' && !prev.node) {
          prev.node = node;           // the spawn_start just before names this scope
          if (num(ev.allocated_usd) !== null) prev.allocated = num(ev.allocated_usd);
          open.push(prev);
        } else {
          const a = newGroup('agent', 'g' + i, {node, name: node.split('/').pop() || 'субагент', kind: String(ev.kind || 'subagent'),
            model: '', status: 'running', allocated: num(ev.allocated_usd), returned: null, steps: []});
          items.push(a);
          open.push(a);
        }
        lastAgentAt = i;
        break;
      }
      case 'subagent_end': {
        const a = open.find(x => x.node === String(ev.node || ''))
          || items.find(x => x.type === 'agent' && x.node === String(ev.node || ''));
        if (a) {
          if (a.kind !== 'spawn' || a.status === 'running') a.status = 'closed';
          a.returned = num(ev.returned_usd);
          close(a);
        }
        act = null;
        break;
      }
      case 'spawn_start': {
        act = null;
        const prev = items[items.length - 1];
        let a;
        if (lastAgentAt === i - 1 && prev && prev.type === 'agent' && prev.kind !== 'spawn' && prev.kind !== 'workflow') {
          a = prev;                   // the budget scope opened just before belongs to this spawn
          a.kind = 'spawn';
          a.name = String(ev.name || a.name);
        } else {
          a = newGroup('agent', 'g' + i, {node: null, name: String(ev.name || 'субагент'), kind: 'spawn', model: '',
            status: 'running', allocated: null, returned: null, steps: []});
          items.push(a);
        }
        a.model = deepShortModel(deepModelName(ev.model));
        if (num(ev.budget_usd) !== null) a.allocated = num(ev.budget_usd);
        byName['spawn:' + a.name] = a;
        lastAgentAt = i;
        break;
      }
      case 'spawn_end': {
        const a = byName['spawn:' + String(ev.name || '')];
        if (a) {
          a.status = ev.status === 'ok' || ev.status === 'success' || !ev.status ? 'closed' : String(ev.status);
          if (ev.model) a.model = deepShortModel(deepModelName(ev.model));
          if (num(ev.spent_usd) !== null) a.spent = num(ev.spent_usd);
          close(a);
        }
        act = null;
        break;
      }
      case 'workflow_start': {
        act = null;
        const a = newGroup('agent', 'g' + i, {node: null, name: String(ev.workflow || 'workflow'), kind: 'workflow', model: '',
          status: 'running', allocated: num(ev.budget_usd), returned: null, steps: []});
        items.push(a);
        byName['workflow:' + a.name] = a;
        break;
      }
      case 'workflow_end': {
        const a = byName['workflow:' + String(ev.workflow || '')];
        if (a) { a.status = ev.status && ev.status !== 'ok' ? String(ev.status) : 'closed'; if (num(ev.spent_usd) !== null) a.spent = num(ev.spent_usd); }
        act = null;
        break;
      }
      case 'todos':
        if (!planShown) { planShown = true; act = null; items.push({type: 'plan', key: 'p' + i}); }
        break;
      case 'hitl_decision':
        act = null;
        items.push(Object.assign({type: 'decision', key: 'd' + i}, deepDecisionText(ev), {point: ev.point}));
        break;
      case 'budget_wrap_up':
        act = null;
        items.push({type: 'note', key: 'n' + i, cls: 'warn', text: 'бюджет: осталось только на отчёт'});
        break;
      case 'budget_refused':
      case 'subagent_refused':
        act = null;
        items.push({type: 'note', key: 'n' + i, cls: 'warn',
          text: (k === 'budget_refused' ? 'бюджет: вызов отклонён' : 'субагент не запущен') + (ev.reason ? ' — ' + deepClip(ev.reason, 140) : '')});
        break;
      case 'user_message':
        act = null;
        items.push({type: 'user', key: 'u' + i, text: String(ev.text || '')});
        break;
      case 'assistant_message':
        act = null;
        items.push({type: 'assistant', key: 'm' + i, text: String(ev.text || '') + (ev.truncated ? '\n\n… (ответ обрезан)' : '')});
        break;
      case 'followup_unanswered':
        act = null;
        items.push({type: 'note', key: 'n' + i, cls: 'warn', text: String(ev.text || '')});
        break;
      case 'followup_abandoned':
        act = null;
        items.push(ev.error
          ? {type: 'note', key: 'n' + i, cls: 'warn', text: 'сообщение не выполнено, отчёт прежний: ' + deepClip(ev.error, 400)}
          : {type: 'note', key: 'n' + i, cls: '', text: 'сообщение отменено, отчёт прежний'});
        break;
      case 'segment_start':
        act = null;
        break;
      case 'segment_end':
        act = null;
        if (ev.status === 'error' || ev.error) items.push({type: 'note', key: 'n' + i, cls: 'err', text: 'ошибка: ' + deepClip(ev.error || ev.status, 400)});
        break;
      case 'done':
        act = null;
        items.push({type: 'done', key: 'done' + i, status: ev.status || '', verdict: ev.verdict || '', trust: ev.trust || null,
          cap: num(ev.confidence_cap), error: ev.error || '', languageClass: ev.language_class || ''});
        break;
      default:
        if (/^workflow_step_/.test(k)) {
          const a = byName['workflow:' + String(ev.workflow || '')];
          if (a) a.steps.push({step: String(ev.step || '?'), state: k.replace('workflow_step_', ''), status: ev.status || ''});
        } else if (/refused$/.test(k)) {
          act = null;
          items.push({type: 'note', key: 'n' + i, cls: 'warn', text: `${k.replace(/_/g, ' ')}: ${deepClip([ev.name, ev.workflow, ev.reason,
            Array.isArray(ev.problems) ? ev.problems.join('; ') : ev.problems].filter(Boolean).join(' · '), 160)}`});
        }
        break;
    }
  });
  return items;
}

// The collapsed row of an activity / agent item: "4 инструмента · haiku · $0.0040".
function deepActivityLine(g) {
  const n = g.tools.length;
  const parts = [];
  if (n) parts.push(`${n} ${deepPlural(n, ['инструмент', 'инструмента', 'инструментов'])}`);
  else if (g.modelCalls) parts.push(`${g.modelCalls} ${deepPlural(g.modelCalls, ['вызов', 'вызова', 'вызовов'])} модели`);
  if (g.models.length) parts.push(g.models.join(', '));
  const cost = num(g.spent) ?? g.cost;
  if (cost > 0) parts.push(fmtUsd(cost));
  const trusts = DEEP_TRUST_LEVELS.filter(t => t !== 'not_verified' && g.tools.some(x => x.ok && x.trust === t));
  return {text: parts.join(' · '), errors: g.errors, trusts};
}

// The slim budget line of the chat header: "$0.0107 / $1 · сегодня $0.01 / $20".
function deepBudgetLine(budget, mode) {
  const b = budget || {};
  const spent = num(b.spent_usd), limit = num(b.limit_usd);
  const d = b.day || {};
  const ds = num(d.spent_usd), dl = num(d.limit_usd);
  const parts = [];
  if (limit !== null) parts.push(`${fmtUsd(spent ?? 0)} / ${deepUsdShort(limit)}`);
  if (dl !== null) parts.push(`сегодня ${deepUsdShort(ds ?? 0)} / ${deepUsdShort(dl)}`);
  const notes = [];
  if (b.exhausted) notes.push('бюджет задачи исчерпан');
  else if (b.wrap_up) notes.push('осталось только на отчёт');
  if (num(b.reserve_usd)) notes.push(`резерв на отчёт ${fmtUsd(b.reserve_usd)}`);
  if (mode === 'mock') notes.push('mock: оценка по токенам, ничего не списано');
  if (d.can_start === false) notes.push('дневной лимит: новый live-запуск будет отклонён');
  const warn = !!(b.exhausted || b.wrap_up || d.can_start === false || (limit && spent !== null && spent / limit >= 0.8));
  return {text: parts.join(' · '), title: notes.join('\n'), warn};
}

// One line under the composer for a live run (GET /api/deep/estimate).
function deepEstimateLine(est, form) {
  if (form.mode !== 'live') return {text: '', cls: ''};
  if (!est) return {text: 'live: платные вызовы API · оценка недоступна', cls: 'strong'};
  const day = est.day || {};
  const r = est.run || {};
  const parts = [`live: платные вызовы API, не больше ${deepUsdShort(est.cap_usd)} за задачу`];
  if (num(r.estimated_cost_usd) !== null) parts.push(`обычно ~${fmtUsd(r.estimated_cost_usd)}`);
  if (num(day.limit_usd) !== null) parts.push(`сегодня ${deepUsdShort(day.spent_usd ?? 0)} / ${deepUsdShort(day.limit_usd)}`);
  if (day.can_start === false) return {text: 'дневной лимит почти исчерпан: сервер откажет в live-запуске', cls: 'blocked'};
  return {text: parts.join(' · '), cls: 'strong'};
}

function deepRunTitle(r, text) {
  const own = r && (r.title || r.task_title || r.task_preview);
  if (own) return deepTitleFromText(own);
  if (text) return deepTitleFromText(text);
  const inp = (r && r.input) || {};
  if (inp.kind && inp.kind !== 'text') return `${String(inp.kind).toUpperCase()} · ${inp.name || 'файл'}`;
  return (r && r.run_id) || 'задача';
}
function deepRunSub(r) {
  const pp = r.pending_point;
  const point = pp && typeof pp === 'object' ? pp.point : pp;
  const st = r.status === 'awaiting_human' && point && DEEP_POINT[point]
    ? 'ждёт: ' + DEEP_POINT[point].name : (DEEP_STATUS_RU[r.status] || r.status || '');
  return [st, r.mode === 'live' ? 'live' : '', fmtWhen(toMs(r.created))].filter(Boolean).join(' · ');
}
// ---- deep: pure helpers end ----

//---------------------------------------------------------------------------
// State + DOM refs
//---------------------------------------------------------------------------
const deep = {
  inited: false,
  settings: null,        // GET /api/deep/settings .settings
  templates: DEEP_TEMPLATES_FALLBACK.slice(),
  mocks: [],
  runs: [],
  runId: null,
  detail: null,          // last GET /api/deep/runs/<id>
  events: [],
  cursor: 0,
  pollGen: 0,
  pollTimer: null,
  file: null,            // {name, mime, size, label, b64}
  taskText: {},          // run id -> the task text (for titles and the first message)
  open: new Set(),       // keys of expanded chat rows
  feedKey: null,
  cardKey: null,         // the review card on screen (kept while the reviewer edits)
  panelKey: null,
  panelView: 'report',   // report | log
  reportClosed: false,
  reportWide: false,
  viewTpl: null,         // {runId, template, urls}: a finished report re-rendered for viewing
  legacyCreate: false,   // the server has no review_mode: fold it into settings_override / auto_approve
  estSeq: 0,
  estTimer: null,
  estimate: null,
  dispatching: false,
  leanAsk: false,        // live: the Lean button's inline confirmation is open
  pendingMessage: null,  // {text, at}: a follow-up on its way, shown until its user_message event
  baseTitle: document.title,
};

const modeSwitch = $('mode-switch');
const deepView = $('deep-view');
const deepText = $('deep-text');
const deepDrop = $('deep-drop');
const deepFileInput = $('deep-file-input');
const deepTemplateSel = $('deep-template');
const deepMainModelSel = $('deep-main-model');
const deepBudgetInp = $('deep-budget');
const deepReviewSel = $('deep-review-mode');
const deepModeSel = $('deep-mode');
const deepMockInline = $('deep-mock-inline');
const deepEstimateEl = $('deep-estimate');
const deepConfirm = $('deep-confirm');
const deepRunBtn = $('deep-run');
const deepMsgEl = $('deep-msg');
const deepRunList = $('deep-run-list');
const deepFeed = $('deep-feed');
const deepTail = $('deep-tail');
const deepStream = $('deep-stream');
const deepReportBody = $('deep-report');
const deepLogEl = $('deep-log');
const deepCancelBtn = $('deep-cancel');
const deepSettingsBack = $('deep-settings-back');
const deepSettingsBody = $('deep-settings-body');
const deepSettingsStatus = $('deep-settings-status');

function isDeepMode() { return document.body.classList.contains('mode-deep'); }

function deepMsg(text, kind) {
  deepMsgEl.textContent = text || '';
  deepMsgEl.className = 'dk-msg' + (kind ? ' ' + kind : '');
}
function trustChip(t, title) {
  const c = el('span', deepTrustClass(t), deepTrustLabel(t));
  if (title) c.title = title;
  return c;
}
function deepFileUrl(rel, runId) {
  return `${API}/deep/runs/${encodeURIComponent(runId || deep.runId)}/files/${rel}`;
}
function deepBtn(label, cls, onClick, title) {
  const b = el('button', 'dk-btn' + (cls ? ' ' + cls : ''), label);
  b.type = 'button';
  if (title) b.title = title;
  if (onClick) b.addEventListener('click', onClick);
  return b;
}

//---------------------------------------------------------------------------
// Workspace switch (Pipelines | Deep Agent)
//---------------------------------------------------------------------------
function setMode(mode) {
  const on = mode === 'deep';
  document.body.classList.toggle('mode-deep', on);
  modeSwitch.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === (on ? 'deep' : 'pipelines')));
  try { localStorage.setItem('tfl-lab-mode', on ? 'deep' : 'pipelines'); } catch (e) { /* storage blocked */ }
  try { history.replaceState(null, '', location.pathname + location.search + (on ? '#deep' : '')); } catch (e) { /* ignore */ }
  if (on) deepInit();
}
modeSwitch.querySelectorAll('.mode-btn').forEach(b => b.addEventListener('click', () => setMode(b.dataset.mode)));

async function deepInit() {
  if (deep.inited) { deepRefreshRuns(); return; }
  deep.inited = true;
  await Promise.allSettled([deepLoadSettings(), deepLoadMocks()]);
  deepSyncControls();
  deepRefreshEstimate();
  await deepRefreshRuns(true);
  deepRenderAll();
}

// The browser tab shows ⏸ while a deep run waits for a decision.
function deepUpdateDocTitle() {
  const waiting = deep.runs.some(r => r.status === 'awaiting_human');
  document.title = (waiting ? '⏸ ' : '') + deep.baseTitle;
}

//---------------------------------------------------------------------------
// Composer controls
//---------------------------------------------------------------------------
async function deepLoadSettings() {
  try {
    const d = await api('/deep/settings');
    deep.settings = d.settings || null;
    if (Array.isArray(d.templates) && d.templates.length) deep.templates = d.templates;
    if (d.error) deepMsg('файл настроек Deep Agent некорректен: ' + d.error + ' (live-запуски отклоняются, пока он не исправлен)', 'err');
  } catch (e) {
    deepMsg(e.status === 503 ? e.message : 'не удалось загрузить настройки Deep Agent — ' + e.message, 'err');
  }
  deepFillControls();
}

function deepFillControls() {
  const s = deep.settings || {};
  const tplNow = deepTemplateSel.value;
  deepTemplateSel.textContent = '';
  deepTemplateSel.appendChild(new Option('шаблон: авто', ''));
  deep.templates.forEach(t => deepTemplateSel.appendChild(new Option('шаблон: ' + t, t)));
  deepTemplateSel.value = deep.templates.includes(tplNow) ? tplNow : '';
  const models = s.models || {};
  const roles = models.roles || {};
  const opts = Array.from(new Set([...(Array.isArray(models.allowed) ? models.allowed : []), roles.main].filter(Boolean)));
  const modelNow = deepMainModelSel.value;
  deepMainModelSel.textContent = '';
  deepMainModelSel.appendChild(new Option('модель: авто' + (roles.main ? ` (${deepShortModel(roles.main)})` : ''), ''));
  opts.forEach(m => {
    const o = new Option('модель: ' + deepShortModel(m), m);
    o.title = m;
    deepMainModelSel.appendChild(o);
  });
  deepMainModelSel.value = opts.includes(modelNow) ? modelNow : '';
  const b = s.budget || {};
  deepBudgetInp.value = b.per_task_usd ?? '';
  deepBudgetInp.title = `бюджет задачи, $ (в настройках ${deepUsdShort(b.per_task_usd)}; на день ${deepUsdShort(b.per_day_usd)})`;
  deepReviewSel.textContent = '';
  DEEP_REVIEW_MODES.forEach(([v, label]) => deepReviewSel.appendChild(new Option(label, v)));
  deepReviewSel.value = deepReviewModeOf(s);
}

async function deepLoadMocks() {
  try {
    const d = await api('/deep/mock-scripts');
    deep.mocks = Array.isArray(d.mock_scripts) ? d.mock_scripts : [];
  } catch (e) { deep.mocks = []; }
  const now = deepModeSel.value;
  deepModeSel.textContent = '';
  deep.mocks.forEach(m => {
    const o = new Option(`mock · ${m.name.replace(/\.json$/, '')}`, 'mock:' + m.name);
    o.title = [m.description, m.task].filter(Boolean).join('\n');
    deepModeSel.appendChild(o);
  });
  deepModeSel.appendChild(new Option('mock · свой сценарий (JSON)', 'inline'));
  deepModeSel.appendChild(new Option('live · платно', 'live'));
  if (now && Array.from(deepModeSel.options).some(o => o.value === now)) deepModeSel.value = now;
}

function deepModeValue() {
  const v = deepModeSel.value || '';
  if (v === 'live') return {mode: 'live', mockName: null};
  if (v === 'inline') return {mode: 'inline', mockName: null};
  return {mode: 'mock', mockName: v.startsWith('mock:') ? v.slice(5) : null};
}

function deepFormValues() {
  const m = deepModeValue();
  const reviewMode = deepReviewSel.value || 'all';
  const legacy = deep.legacyCreate;
  const base = {perTaskUsd: deepBudgetInp.value, mainModel: deepMainModelSel.value || null};
  if (legacy) {   // an older server: the review mode becomes hitl flags / auto_approve
    base.hitlFormalization = reviewMode !== 'auto';
    base.hitlReport = reviewMode === 'all';
  }
  return {
    text: deepText.value,
    file: deep.file,
    template: deepTemplateSel.value || null,
    mode: m.mode,
    mockName: m.mockName,
    mockInline: deepMockInline.value,
    confirm: deepConfirm.checked,
    reviewMode,
    legacy,
    autoApprove: legacy && reviewMode === 'auto',
    mainModel: deepMainModelSel.value || null,
    savedPerTask: deep.settings && deep.settings.budget ? deep.settings.budget.per_task_usd : null,
    override: deepOverride(base, deep.settings),
  };
}

function deepSyncControls() {
  const m = deepModeValue();
  const live = m.mode === 'live';
  deepMockInline.classList.toggle('show', m.mode === 'inline');
  // an older server has no review_mode: there «Без проверок» means auto_approve, which is mock-only
  const autoOpt = Array.from(deepReviewSel.options).find(o => o.value === 'auto');
  if (autoOpt) {
    autoOpt.disabled = live && deep.legacyCreate;
    if (autoOpt.disabled && deepReviewSel.value === 'auto') deepReviewSel.value = 'all';
  }
  deepReviewSel.classList.toggle('live', live && deepReviewSel.value === 'auto');
  deepReviewSel.title = live && deepReviewSel.value === 'auto'
    ? 'без проверок: формализация и отчёт идут без вас, но денег сверх бюджета агент всё равно спросит'
    : 'когда агент останавливается и ждёт вашего решения';
  $('deep-live-group').classList.toggle('show', live);
  $('deep-live-group').classList.toggle('armed', live && deepConfirm.checked);
  deepRunBtn.classList.toggle('live', live);
  deepRunBtn.title = live ? 'Запустить live (платно; Ctrl+Enter не запускает live)' : 'Отправить (Ctrl+Enter)';
  deepRunBtn.disabled = deep.dispatching || (live && !deepConfirm.checked) || (!!deep.file && !deep.file.b64);
  const sel = deep.mocks.find(x => 'mock:' + x.name === deepModeSel.value);
  deepModeSel.title = sel ? [sel.description, sel.task ? 'задача: ' + sel.task : ''].filter(Boolean).join('\n') : '';
  deepModeSel.classList.toggle('live', live);
  const est = deepEstimateLine(deep.estimate, m);
  deepEstimateEl.textContent = est.text;
  deepEstimateEl.className = 'dk-estimate' + (est.cls ? ' ' + est.cls : '');
  const c = deepComposerNow();
  if (c.kind !== 'new') {   // the form's mode applies only to a new task: the run has its own
    deepRunBtn.classList.toggle('live', c.live);
    deepRunBtn.title = c.live ? 'Отправить (платно; Ctrl+Enter не отправляет live)' : 'Отправить (Ctrl+Enter)';
    deepRunBtn.disabled = deep.dispatching || c.kind !== 'message';
  }
}

function deepComposerNow() { return deepComposerState(deep.detail, deep.runId); }

// The composer continues the open run; only a new task has a file, a template and a mode.
const DEEP_TASK_PLACEHOLDER = deepText.placeholder;
function deepRenderComposer() {
  const c = deepComposerNow();
  $('deep-composer').classList.toggle('followup', c.kind !== 'new');
  deepText.placeholder = c.placeholder || DEEP_TASK_PLACEHOLDER;
  deepText.disabled = c.kind === 'busy' || c.kind === 'closed';
  const note = $('deep-composer-note');
  note.textContent = c.note;
  note.classList.toggle('live', c.live);
  deepSyncControls();
}

function deepRefreshEstimateSoon() {
  if (deep.estTimer) clearTimeout(deep.estTimer);
  deep.estTimer = setTimeout(deepRefreshEstimate, 250);
}
async function deepRefreshEstimate() {
  const seq = ++deep.estSeq;
  const form = deepFormValues();
  const qs = new URLSearchParams();
  if (form.template) qs.set('template', form.template);
  if (form.override) qs.set('settings_override', JSON.stringify(form.override));
  try {
    const est = await api('/deep/estimate' + (qs.toString() ? '?' + qs.toString() : ''));
    if (seq !== deep.estSeq) return;
    deep.estimate = est;
  } catch (e) {
    if (seq !== deep.estSeq) return;
    deep.estimate = null;
  }
  deepSyncControls();
  if (!deep.runId) deepRenderHead();
}

[deepTemplateSel, deepMainModelSel, deepBudgetInp, deepReviewSel].forEach(n =>
  n.addEventListener('change', () => { deepSyncControls(); deepRefreshEstimateSoon(); }));
deepBudgetInp.addEventListener('input', deepRefreshEstimateSoon);
deepModeSel.addEventListener('change', () => {
  if (deepModeSel.value !== 'live') deepConfirm.checked = false;
  deepSyncControls();
  deepRefreshEstimateSoon();
});
deepConfirm.addEventListener('change', deepSyncControls);

//---------------------------------------------------------------------------
// Task input: text, drag-and-drop, file picker, pasted image (base64 JSON upload)
//---------------------------------------------------------------------------
function deepRenderFile() {
  const f = deep.file;
  deepDrop.classList.toggle('has-file', !!f);
  if (f) {
    $('deep-file-kind').textContent = f.label;
    $('deep-file-name').textContent = f.name;
    $('deep-file-size').textContent = (f.size >= 1048576 ? (f.size / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(f.size / 1024)) + ' kB')
      + (f.b64 ? '' : ' · читается…');
  }
  deepSyncControls();
}
function deepClearFile() {
  deep.file = null;
  deepFileInput.value = '';
  deepRenderFile();
}
function deepSetFile(file) {
  if (!file) return;
  if (deepComposerNow().kind !== 'new') { deepMsg('файл можно приложить только к новой задаче (кнопка «Новая»)', 'warn'); return; }
  const kind = deepFileKind(file.name, file.type);
  const maxMb = deep.settings && deep.settings.intake ? deep.settings.intake.max_file_mb : null;
  const problem = deepFileProblem(file.name, file.type, file.size, maxMb);
  if (problem) { deepMsg(problem, 'err'); return; }
  if (kind.kind === 'text') {
    file.text().then(t => {
      deepClearFile();
      deepText.value = t;
      deepMsg(`${file.name} загружен как текст`);
    }).catch(e => deepMsg('не удалось прочитать файл — ' + e.message, 'err'));
    return;
  }
  const ext = {'application/pdf': 'pdf', 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[kind.mime];
  const token = {name: file.name || `pasted.${ext}`, mime: kind.mime, size: file.size, label: kind.label, b64: null};
  deep.file = token;
  deepMsg('');
  const reader = new FileReader();
  reader.onload = () => {
    if (deep.file !== token) return;
    token.b64 = deepB64FromDataUrl(reader.result);
    deepRenderFile();
  };
  reader.onerror = () => {
    if (deep.file !== token) return;
    deepClearFile();
    deepMsg('браузер не смог прочитать файл', 'err');
  };
  reader.readAsDataURL(file);
  deepRenderFile();
}
deepFileInput.addEventListener('change', () => deepSetFile(deepFileInput.files && deepFileInput.files[0]));
$('deep-file-clear').addEventListener('click', deepClearFile);
['dragenter', 'dragover'].forEach(t => deepDrop.addEventListener(t, (e) => {
  e.preventDefault();
  deepDrop.classList.add('over');
}));
['dragleave', 'dragend'].forEach(t => deepDrop.addEventListener(t, () => deepDrop.classList.remove('over')));
deepDrop.addEventListener('drop', (e) => {
  e.preventDefault();
  deepDrop.classList.remove('over');
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) deepSetFile(f);
});
// A file dropped next to the box must not make the browser navigate away from the tab.
['dragover', 'drop'].forEach(t => deepView.addEventListener(t, (e) => { if (!deepDrop.contains(e.target)) e.preventDefault(); }));
deepText.addEventListener('paste', (e) => {
  const files = e.clipboardData && e.clipboardData.files;
  if (files && files.length && /^image\//.test(files[0].type)) {
    e.preventDefault();
    deepSetFile(files[0]);
  }
});
// the textarea grows with its text (up to a cap set in lab.css)
function deepAutoGrow() {
  deepText.style.height = 'auto';
  deepText.style.height = Math.min(deepText.scrollHeight, 220) + 'px';
}
deepText.addEventListener('input', deepAutoGrow);

//---------------------------------------------------------------------------
// Start / cancel / new task
//---------------------------------------------------------------------------
async function deepStartRun() {
  if (deep.dispatching) return;
  let form = deepFormValues();
  let res = deepCreateBody(form);
  if (res.error) { deepMsg(res.error, 'err'); return; }
  deep.dispatching = true;
  deepSyncControls();
  deepMsg(form.mode === 'live' ? 'запускаю live…' : 'запускаю mock…');
  const textNow = form.file ? '' : String(form.text || '').trim();
  try {
    let d;
    try {
      d = await sendJson('/deep/runs', res.body);
    } catch (e) {
      // a server without review_mode refuses the unknown field (400): fold it into the old fields once
      if (e.status === 400 && /review_mode/.test(e.message) && !deep.legacyCreate) {
        deep.legacyCreate = true;
        form = deepFormValues();
        res = deepCreateBody(form);
        if (res.error) throw new Error(res.error);
        d = await sendJson('/deep/runs', res.body);
      } else {
        throw e;
      }
    }
    const warn = Array.isArray(d.warnings) && d.warnings.length ? ' · ' + d.warnings.join('; ') : '';
    deepMsg(warn ? 'предупреждения intake' + warn : '', warn ? 'warn' : '');
    if (textNow) deep.taskText[d.run_id] = textNow;
    if (form.mode === 'live') deepConfirm.checked = false;   // every paid run is confirmed again
    deepText.value = '';
    deepAutoGrow();
    deepClearFile();
    deepOpenRun(d.run_id);
    deepRefreshRuns();
  } catch (e) {
    deepMsg(e.message, 'err');
  } finally {
    deep.dispatching = false;
    deepSyncControls();
  }
}
// A follow-up to the open run: `body` from deepMessageBody. True when the server took it.
async function deepPostMessage(body) {
  if (deep.dispatching) return false;
  deep.dispatching = true;
  deepSyncControls();
  deepMsg('');
  const id = deep.runId;
  try {
    await sendJson(`/deep/runs/${encodeURIComponent(id)}/message`, body);
    if (id !== deep.runId) return true;
    deep.leanAsk = false;
    deep.pendingMessage = {text: body.text || '', at: deep.events.length};
    deepStartPolling();
    deepRefreshRuns();
    return true;
  } catch (e) {
    deepMsg(e.message, 'err');
    return false;
  } finally {
    deep.dispatching = false;
    deepSyncControls();
  }
}

async function deepSendFollowup() {
  // a click on the button is the confirmation of a paid message; Ctrl+Enter never gets here on a live run
  const res = deepMessageBody(deepText.value, {live: deepComposerNow().live, confirm: true});
  if (res.error) { deepMsg(res.error, 'err'); return; }
  if (await deepPostMessage(res.body)) {
    deepText.value = '';
    deepAutoGrow();
  }
}

function deepSend() {
  const kind = deepComposerNow().kind;
  if (kind === 'message') deepSendFollowup();
  else if (kind === 'new') deepStartRun();
}

// Ctrl/Cmd+Enter sends a mock task or a mock follow-up; anything live needs the button
function deepRunShortcut() {
  const c = deepComposerNow();
  if (c.kind === 'message' && c.live) { deepMsg('live: сообщение — только кнопкой отправки', 'warn'); return; }
  if (c.kind === 'new' && deepModeValue().mode === 'live') { deepMsg('live-запуск — только кнопкой отправки (Ctrl+Enter запускает mock)', 'warn'); return; }
  deepSend();
}
deepRunBtn.addEventListener('click', deepSend);

async function deepCancelRun() {
  const r = deep.detail && deep.detail.run;
  if (!r) return;
  const followup = !!(deep.detail.followup && deep.detail.followup.in_progress);
  if (r.status === 'awaiting_human' && !followup
      && !window.confirm('Остановить запуск? Запуск, ждущий решения, потом нельзя продолжить.')) return;
  deepCancelBtn.disabled = true;
  try {
    await sendJson(`/deep/runs/${encodeURIComponent(r.run_id)}/cancel`, {});
    deepStartPolling();
    deepRefreshRuns();
  } catch (e) {
    deepMsg('не удалось остановить — ' + e.message, 'err');
  } finally {
    deepCancelBtn.disabled = false;
  }
}
deepCancelBtn.addEventListener('click', deepCancelRun);

function deepNewTask() {
  deepStopPolling();
  Object.assign(deep, {runId: null, detail: null, events: [], cursor: 0, feedKey: null, cardKey: null, panelKey: null,
    reportClosed: false, viewTpl: null, leanAsk: false, pendingMessage: null});
  deep.open = new Set();
  deepRenderRuns();
  deepRenderAll();
  deepText.focus();
}
$('deep-new').addEventListener('click', deepNewTask);

//---------------------------------------------------------------------------
// Runs list (left)
//---------------------------------------------------------------------------
async function deepRefreshRuns(first) {
  let d;
  try { d = await api('/deep/runs'); } catch (e) { return; }
  deep.runs = Array.isArray(d.runs) ? d.runs : [];
  deepRenderRuns();
  deepUpdateDocTitle();
  deepLoadTitles();
  if (first && !deep.runId) {
    const act = deep.runs.find(x => deepIsActive(x.status) || x.status === 'awaiting_human');
    if (act) deepOpenRun(act.run_id);
  }
}
// the task text of text runs (titles and the first chat message), fetched once per run
function deepLoadTitles() {
  deep.runs.slice(0, 60).forEach(r => {
    const inp = r.input || {};
    if (r.title || r.task_title || r.task_preview || inp.kind !== 'text' || !inp.file || r.run_id in deep.taskText) return;
    deep.taskText[r.run_id] = null;
    fetch(deepFileUrl(inp.file, r.run_id), {cache: 'no-store'})
      .then(x => (x.ok ? x.text() : Promise.reject(new Error(String(x.status)))))
      .then(t => {
        deep.taskText[r.run_id] = t;
        deepRenderRuns();
        if (r.run_id === deep.runId) { deep.feedKey = null; deepRenderHead(); deepRenderFeed(); }
      })
      .catch(() => { deep.taskText[r.run_id] = ''; });
  });
}
function deepRenderRuns() {
  $('deep-run-count').textContent = deep.runs.length ? String(deep.runs.length) : '';
  deepRunList.textContent = '';
  if (!deep.runs.length) {
    deepRunList.appendChild(el('div', 'dk-runs-empty', 'запусков пока нет'));
    return;
  }
  deep.runs.forEach(r => {
    const item = el('button', 'dk-run' + (r.run_id === deep.runId ? ' active' : ''));
    item.type = 'button';
    item.dataset.id = r.run_id;
    item.appendChild(el('span', 'dk-dot ' + (r.status || '')));
    item.appendChild(el('span', 'dk-run-title', deepRunTitle(r, deep.taskText[r.run_id])));
    item.appendChild(el('span', 'dk-run-cost', num(r.spent_usd) === null ? '' : fmtUsd(r.spent_usd)));
    const sub = deepRunSub(r);
    item.appendChild(el('span', 'dk-run-sub', sub));
    item.title = [r.run_id, sub, r.verdict ? 'вердикт: ' + r.verdict : '', r.error || ''].filter(Boolean).join('\n');
    item.addEventListener('click', () => deepOpenRun(r.run_id));
    deepRunList.appendChild(item);
  });
}
setInterval(() => {
  if (document.hidden || !isDeepMode() || !deep.inited) return;
  if (deep.runs.some(x => deepIsActive(x.status))) deepRefreshRuns();
}, 3000);

//---------------------------------------------------------------------------
// Opening a run + polling GET /api/deep/runs/<id>?after=N
//---------------------------------------------------------------------------
function deepStopPolling() {
  deep.pollGen++;
  if (deep.pollTimer) { clearTimeout(deep.pollTimer); deep.pollTimer = null; }
}
function deepOpenRun(id) {
  deepStopPolling();
  Object.assign(deep, {runId: id, detail: null, events: [], cursor: 0, feedKey: null, cardKey: null, panelKey: null,
    reportClosed: false, viewTpl: null, panelView: 'report', leanAsk: false, pendingMessage: null});
  deep.open = new Set();
  deepRunList.querySelectorAll('.dk-run').forEach(n => n.classList.toggle('active', n.dataset.id === id));
  deepRenderAll();
  deepStartPolling();
}
function deepStartPolling() {
  const gen = ++deep.pollGen;
  if (deep.pollTimer) { clearTimeout(deep.pollTimer); deep.pollTimer = null; }
  const tick = async () => {
    if (gen !== deep.pollGen) return;
    let again = true;
    try { again = await deepPollOnce(gen); }
    catch (e) {
      if (e.status === 404) { again = false; deepMsg(`запуск ${deep.runId} не найден — ${e.message}`, 'err'); }
    }
    if (gen !== deep.pollGen) return;
    deep.pollTimer = again ? setTimeout(tick, 800) : null;
  };
  tick();
}
async function deepPollOnce(gen) {
  const id = deep.runId;
  const d = await api(`/deep/runs/${encodeURIComponent(id)}?after=${deep.cursor}`);
  if (gen !== deep.pollGen || id !== deep.runId) return false;
  const evs = Array.isArray(d.events) ? d.events : [];
  if (evs.length) deep.events = deep.events.concat(evs);
  deep.cursor = num(d.next) ?? (deep.cursor + evs.length);
  const before = deep.detail;
  const prev = before && before.run ? before.run.status : null;
  // a follow-up replaced the report: show the new one (a view of the old one is gone with it)
  const revBefore = before && before.result ? before.result.report_revision : null;
  const rev = d.result ? d.result.report_revision : null;
  if (revBefore !== null && revBefore !== undefined && rev !== null && rev !== undefined && rev !== revBefore) {
    deep.viewTpl = null;
    deep.reportClosed = false;
    deep.panelKey = null;
  }
  deep.detail = d;
  const st = d.run ? d.run.status : null;
  if (st !== prev && prev !== null) deepRefreshRuns();
  deepRenderAll();
  return deepIsActive(st);
}

function deepRenderAll() {
  deepView.classList.toggle('empty', !deep.runId);
  deepRenderHead();
  deepRenderFeed();
  deepRenderTail();
  deepRenderPanel();
  deepRenderComposer();
}

//---------------------------------------------------------------------------
// Chat header: title, status, budget line, actions
//---------------------------------------------------------------------------
function deepRenderHead() {
  const d = deep.detail;
  const r = d && d.run;
  const st = r ? r.status : '';
  const runMeta = deep.runs.find(x => x.run_id === deep.runId) || r;
  $('dh-title').textContent = deep.runId ? deepRunTitle(runMeta || {run_id: deep.runId}, deep.taskText[deep.runId]) : 'Новая задача';
  const pill = $('dh-status');
  pill.textContent = r ? (DEEP_STATUS_RU[st] || st) : '';
  pill.className = 'dk-pill ' + (st || '') + (r ? '' : ' hidden');
  const mode = $('dh-mode');
  mode.textContent = r ? (r.live ? 'live' : 'mock') : '';
  mode.className = 'dk-tag' + (r && r.live ? ' live' : '') + (r ? '' : ' hidden');
  const budget = d ? d.budget : (deep.estimate ? {day: deep.estimate.day} : null);
  const b = deepBudgetLine(budget, r ? r.mode : null);
  const bl = $('deep-budget-line');
  bl.textContent = b.text;
  bl.title = b.title;
  bl.classList.toggle('warn', b.warn);
  deepCancelBtn.style.display = r && (deepIsActive(st) || st === 'awaiting_human') ? '' : 'none';
  $('deep-log-open').style.display = deep.runId ? '' : 'none';
  const src = deepPanelSource();
  $('deep-report-open').style.display = src && deep.reportClosed ? '' : 'none';
}
$('deep-report-open').addEventListener('click', () => deepShowPanel('report'));
$('deep-log-open').addEventListener('click', () => deepShowPanel(deep.panelView === 'log' && deepPanelVisible() ? null : 'log'));

//---------------------------------------------------------------------------
// Chat feed: the task, the activity rows, the plan, decisions, notes, the final card
//---------------------------------------------------------------------------
function deepAtBottom() {
  return deepStream.scrollTop + deepStream.clientHeight >= deepStream.scrollHeight - 40;
}
function deepScrollDown() { deepStream.scrollTop = deepStream.scrollHeight; }

function deepRenderFeed(force) {
  const fu = deep.detail && deep.detail.followup;
  const stillSent = deep.pendingMessage && deepIsActive(deep.detail && deep.detail.run ? deep.detail.run.status : 'queued')
    && !deep.events.slice(deep.pendingMessage.at).some(e => e.event === 'user_message');
  if (!stillSent) deep.pendingMessage = null;
  const key = `${deep.runId}:${deep.events.length}:${[...deep.open].join(',')}:${deep.detail ? (deep.detail.todos || []).length : -1}`
    + `:${deep.detail && deep.detail.result ? 'r' + deep.detail.result.report_revision : ''}:${deep.taskText[deep.runId] ? 't' : ''}`
    + `:${fu && fu.lean && fu.lean.available ? 'lean' : ''}:${deep.leanAsk ? 'ask' : ''}:${deep.pendingMessage ? 'sent' : ''}`;
  if (!force && key === deep.feedKey) return;
  const wasBottom = deepAtBottom();
  deep.feedKey = key;
  deepFeed.textContent = '';
  if (!deep.runId) {
    deepFeed.appendChild(deepWelcome());
    return;
  }
  deepFeed.appendChild(deepUserMessage());
  const items = deepChatItems(deep.events);
  const lastDone = items.filter(it => it.type === 'done').pop();
  if (lastDone) lastDone.last = true;
  if (deep.pendingMessage && deep.pendingMessage.text) items.push({type: 'user', key: 'sent', text: deep.pendingMessage.text});
  const frag = document.createDocumentFragment();
  items.forEach(it => {
    const n = deepItemNode(it);
    if (n) frag.appendChild(n);
  });
  deepFeed.appendChild(frag);
  if (wasBottom || force === undefined) deepScrollDown();
}

function deepWelcome() {
  const box = el('div', 'dk-welcome');
  box.appendChild(el('div', 'dk-welcome-title', 'Deep Agent'));
  box.appendChild(el('div', 'dk-welcome-sub',
    'Один агент для REG, CFL, DCFL и LL(k). Опишите задачу текстом или перетащите PDF / изображение — агент формализует её, проверит инструментами и напишет отчёт.'));
  const ex = deep.mocks.filter(m => m.task);
  if (ex.length) {
    const list = el('div', 'dk-examples');
    ex.forEach(m => {
      const b = el('button', 'dk-example');
      b.type = 'button';
      b.appendChild(el('span', 'dk-example-tag', m.name.split('_')[0]));
      b.appendChild(el('span', 'dk-example-text', deepClip(m.task, 110)));
      b.title = 'вставить задачу и выбрать mock-сценарий ' + m.name;
      b.addEventListener('click', () => {
        deepText.value = m.task;
        deepAutoGrow();
        deepModeSel.value = 'mock:' + m.name;
        deepConfirm.checked = false;
        deepSyncControls();
        deepText.focus();
      });
      list.appendChild(b);
    });
    box.appendChild(el('div', 'dk-examples-title', 'Примеры (mock, бесплатно)'));
    box.appendChild(list);
  }
  return box;
}

function deepUserMessage() {
  const r = (deep.detail && deep.detail.run) || deep.runs.find(x => x.run_id === deep.runId) || {};
  const inp = r.input || {};
  const msg = el('div', 'dk-msg-user');
  const bubble = el('div', 'dk-bubble');
  if (inp.kind && inp.kind !== 'text') {
    const chip = el('a', 'dk-filechip');
    chip.href = inp.file ? deepFileUrl(inp.file) : '#';
    chip.target = '_blank';
    chip.rel = 'noopener';
    chip.appendChild(el('span', 'dk-filechip-kind', String(inp.kind).toUpperCase() === 'FILE' ? (inp.mime || 'file') : String(inp.kind).toUpperCase()));
    chip.appendChild(el('span', 'dk-filechip-name', inp.name || 'файл'));
    const meta = [num(inp.pages) !== null ? `${inp.pages} стр.` : '', num(inp.size_bytes) !== null ? Math.max(1, Math.round(inp.size_bytes / 1024)) + ' kB' : '']
      .filter(Boolean).join(' · ');
    if (meta) chip.appendChild(el('span', 'dk-filechip-meta', meta));
    bubble.appendChild(chip);
  } else {
    const t = deep.taskText[deep.runId];
    const text = t === undefined || t === null ? (r.title || r.task_preview || '…') : t;
    const long = String(text).length > 700;
    const body = el('div', 'dk-bubble-text' + (long ? ' clamped' : ''), text || '(пустая задача)');
    bubble.appendChild(body);
    if (long) {
      const more = el('button', 'dk-linkbtn', 'показать полностью');
      more.type = 'button';
      more.addEventListener('click', () => { body.classList.toggle('clamped'); more.textContent = body.classList.contains('clamped') ? 'показать полностью' : 'свернуть'; });
      bubble.appendChild(more);
    }
  }
  msg.appendChild(bubble);
  const meta = [r.template ? 'шаблон ' + r.template : '', fmtWhen(toMs(r.created))].filter(Boolean).join(' · ');
  if (meta) msg.appendChild(el('div', 'dk-msg-meta', meta));
  return msg;
}

function deepToggle(key) {
  if (deep.open.has(key)) deep.open.delete(key); else deep.open.add(key);
  deepRenderFeed(true);
}

function deepItemNode(it) {
  switch (it.type) {
    case 'activity': return deepActivityNode(it, null);
    case 'agent': return deepActivityNode(it, it);
    case 'plan': return deepPlanNode(it);
    case 'decision': return el('div', 'dk-line decision ' + it.kind, it.text);
    case 'note': {
      const n = el('div', 'dk-line note ' + (it.cls || ''), it.text);
      if (it.cls === 'err') {
        const lb = el('button', 'dk-linkbtn', 'журнал');
        lb.type = 'button';
        lb.addEventListener('click', () => deepShowPanel('log'));
        n.appendChild(lb);
      }
      return n;
    }
    case 'done': return deepDoneNode(it);
    case 'user': {
      const msg = el('div', 'dk-msg-user');
      const bubble = el('div', 'dk-bubble');
      bubble.appendChild(el('div', 'dk-bubble-text', it.text));
      msg.appendChild(bubble);
      return msg;
    }
    case 'assistant': {
      const box = el('div', 'md-render dk-agent-text');
      deepRenderMarkdownInto(box, it.text);
      return box;
    }
    default: return null;
  }
}

function deepActivityNode(g, agent) {
  const opened = deep.open.has(g.key);
  const box = el('div', 'dk-act' + (agent ? ' agent ' + (agent.status || '') : '') + (opened ? ' open' : ''));
  const row = el('button', 'dk-act-row');
  row.type = 'button';
  row.setAttribute('aria-expanded', opened ? 'true' : 'false');
  row.appendChild(el('span', 'dk-caret', opened ? '▾' : '▸'));
  const line = deepActivityLine(g);
  if (agent) {
    const kind = agent.kind === 'workflow' ? 'workflow' : (agent.kind === 'branch' ? 'ветка' : 'субагент');
    row.appendChild(el('span', 'dk-act-who', `${kind} ${agent.name}`));
    if (agent.model && !g.models.includes(agent.model)) line.text = [agent.model, line.text].filter(Boolean).join(' · ');
  }
  row.appendChild(el('span', 'dk-act-text', line.text || (agent ? '' : 'работа агента')));
  line.trusts.slice(0, 2).forEach(t => row.appendChild(trustChip(t)));
  if (line.errors) row.appendChild(el('span', 'dk-act-err', `${line.errors} ${deepPlural(line.errors, ['ошибка', 'ошибки', 'ошибок'])}`));
  if (agent && agent.status === 'running') row.appendChild(el('span', 'dk-live-dot'));
  else if (agent && agent.status && agent.status !== 'closed') row.appendChild(el('span', 'dk-act-err', agent.status));
  row.addEventListener('click', () => deepToggle(g.key));
  box.appendChild(row);
  if (opened) {
    const list = el('div', 'dk-act-list');
    if (agent && (agent.allocated !== null || agent.returned !== null)) {
      list.appendChild(el('div', 'dk-act-meta', [agent.allocated !== null ? `выделено ${fmtUsd(agent.allocated)}` : '',
        agent.returned !== null ? `возвращено ${fmtUsd(agent.returned)}` : '', agent.node || ''].filter(Boolean).join(' · ')));
    }
    (agent ? agent.steps : []).forEach(s => list.appendChild(el('div', 'dk-tool', `шаг ${s.step} · ${s.state}${s.status ? ' · ' + s.status : ''}`)));
    g.tools.forEach(t => {
      const tkey = g.key + ':' + t.i;
      const topen = deep.open.has(tkey);
      const tr = el('div', 'dk-tool' + (t.ok ? '' : ' err') + (topen ? ' open' : ''));
      tr.appendChild(el('span', 'dk-tool-mark', t.ok ? '✓' : '✗'));
      tr.appendChild(el('span', 'dk-tool-name', t.name));
      tr.appendChild(trustChip(t.trust, t.reason));
      const why = el('span', 'dk-tool-why', t.error || t.reason);
      why.title = t.error || t.reason;
      tr.appendChild(why);
      if (t.args || t.error) {
        tr.classList.add('clickable');
        tr.addEventListener('click', (e) => { e.stopPropagation(); deepToggle(tkey); });
      }
      list.appendChild(tr);
      if (topen) {
        const det = [t.error ? 'ошибка: ' + t.error : '', t.reason && t.error ? 'trust: ' + t.reason : '', t.args ? 'args: ' + t.args : '', t.id ? 'id: ' + t.id : '']
          .filter(Boolean).join('\n');
        list.appendChild(el('pre', 'dk-tool-detail', det));
      }
    });
    if (!g.tools.length && !(agent && agent.steps.length)) {
      list.appendChild(el('div', 'dk-act-meta', g.modelCalls ? 'только вызовы модели' : 'пока пусто'));
    }
    box.appendChild(list);
  }
  return box;
}

function deepPlanNode() {
  const todos = (deep.detail && Array.isArray(deep.detail.todos)) ? deep.detail.todos : [];
  const box = el('div', 'dk-plan');
  const doneN = todos.filter(t => t && t.status === 'completed').length;
  const head = el('div', 'dk-plan-head');
  head.appendChild(el('span', null, 'План'));
  head.appendChild(el('span', 'dk-plan-count', todos.length ? `${doneN} / ${todos.length}` : ''));
  box.appendChild(head);
  todos.forEach(t => {
    const st = String((t && t.status) || 'pending');
    const row = el('div', 'dk-todo ' + st);
    row.appendChild(el('span', 'dk-todo-mark', st === 'completed' ? '✓' : (st === 'in_progress' ? '◐' : '○')));
    row.appendChild(el('span', 'dk-todo-text', String((t && t.content) || '')));
    box.appendChild(row);
  });
  return box;
}

// «Формально верифицировать (Lean)» under the last final card. Live: a second click confirms, inline.
function deepLeanAction() {
  const d = deep.detail || {};
  const lean = d.followup && d.followup.lean;
  if (!lean || !lean.available) return null;
  const live = !!(d.run && d.run.live);
  const go = () => {
    const res = deepMessageBody('', {preset: 'lean', live, confirm: true});
    if (res.error) deepMsg(res.error, 'err'); else deepPostMessage(res.body);
  };
  const box = el('div', 'dk-card-actions dk-lean');
  if (live && deep.leanAsk) {
    const limit = num(lean.subagent_limit_usd);
    box.appendChild(el('span', 'dk-lean-ask', `Модель ${deepShortModel(deepModelName(lean.model)) || lean.model || '?'}`
      + (limit !== null ? `, лимит субагента ${deepUsdShort(limit)}` : '')));
    box.appendChild(deepBtn('Запустить', 'primary', go));
    box.appendChild(deepBtn('Отмена', 'ghost', () => { deep.leanAsk = false; deepRenderFeed(true); }));
  } else {
    box.appendChild(deepBtn('Формально верифицировать (Lean)', null,
      live ? () => { deep.leanAsk = true; deepRenderFeed(true); } : go));
  }
  return box;
}

function deepDoneNode(it) {
  const d = deep.detail || {};
  // an earlier final card keeps what it said then; only the last one is the current report
  const res = it.last ? (d.result || null) : null;
  const ok = it.status === 'submitted';
  const card = el('div', 'dk-card done' + (ok ? '' : ' bad'));
  const head = el('div', 'dk-card-head');
  head.appendChild(el('span', 'dk-card-title', ok ? 'Отчёт готов' : ({incomplete: 'Отчёт не принят', budget_exhausted: 'Бюджет исчерпан', error: 'Ошибка'}[it.status] || 'Запуск завершён')));
  const spent = it.last ? firstNum(d.budget && d.budget.spent_usd, d.run && d.run.spent_usd) : null;
  if (spent !== null) head.appendChild(el('span', 'dk-card-meta', fmtUsd(spent)));
  card.appendChild(head);
  const line = el('div', 'dk-card-line');
  const verdict = (res && res.verdict) || it.verdict;
  if (verdict) {
    const v = el('span', 'dk-verdict', deepClip(verdict, 120));
    v.title = verdict;
    line.appendChild(v);
  }
  const trust = (res && res.trust) || it.trust;
  if (trust) line.appendChild(trustChip(trust, 'trust выставлен кодом по журналу доказательств (docs/VERDICT_POLICY.md)'));
  const cap = num((res && res.confidence_cap) ?? it.cap);
  if (cap !== null) line.appendChild(el('span', 'dk-card-meta', `cap ${cap.toFixed(2)}`));
  if (line.childNodes.length) card.appendChild(line);
  if (it.error) card.appendChild(el('div', 'dk-card-line err', deepClip(it.error, 300)));
  if (res && res.status && res.status !== 'submitted' && res.reason) card.appendChild(el('div', 'dk-card-line err', deepClip(res.reason, 300)));
  const urls = (res && res.urls) || {};
  if (urls.html || urls.md) {
    const acts = el('div', 'dk-card-actions');
    acts.appendChild(deepBtn('Открыть отчёт', 'primary', () => deepShowPanel('report')));
    card.appendChild(acts);
  }
  const lean = it.last ? deepLeanAction() : null;
  if (lean) card.appendChild(lean);
  return card;
}

//---------------------------------------------------------------------------
// Feed tail: the review card, the "working" line, the end of a run without a done event
//---------------------------------------------------------------------------
// A note line ending with the «журнал» button that opens the log panel.
function deepLogNote(cls, text) {
  const n = el('div', cls, text);
  const lb = el('button', 'dk-linkbtn', 'журнал');
  lb.type = 'button';
  lb.addEventListener('click', () => deepShowPanel('log'));
  n.appendChild(lb);
  return n;
}

function deepRenderTail() {
  const d = deep.detail;
  const run = d && d.run;
  const pv = deepPendingView(d);
  const lastError = d && d.followup ? d.followup.error : null;
  const key = pv ? `${deep.runId}:${pv.interruptId}:${pv.point}`
    : `${deep.runId}:${run ? run.status : ''}:${run ? (run.error || '') : ''}:${lastError || ''}`;
  const wasBottom = deepAtBottom();
  if (key !== deep.cardKey) {
    deep.cardKey = key;
    deepTail.textContent = '';
    if (pv) {
      deepTail.appendChild(pv.point === 'budget' ? deepBudgetCard(pv, run) : deepReviewCard(pv, run));
      if (pv.point === 'report' && !deep.reportClosed) deep.panelKey = null;
    } else if (run && !deepIsActive(run.status)) {
      const inFeed = deep.events.some(e => (e.event === 'done' || e.event === 'segment_end') && e.error === run.error);
      if (run.status === 'cancelled') deepTail.appendChild(el('div', 'dk-line note', 'запуск остановлен'));
      else if (run.status === 'interrupted') deepTail.appendChild(el('div', 'dk-line note warn', 'запуск прерван (сервер перезапускался); результат — в журнале'));
      else if (run.status === 'awaiting_human') deepTail.appendChild(el('div', 'dk-line note warn', 'запуск ждёт решения, но точка остановки не найдена'));
      if (run.error && !inFeed) {
        deepTail.appendChild(deepLogNote('dk-line note err', 'ошибка: ' + deepClip(run.error, 400)));
      }
      // a follow-up the CLI refused has no event of its own
      if (lastError && !deep.events.some(e => e.error === lastError)) {
        // a follow-up that could not be restored closes the composer: the report may not be the previous one
        const said = d.followup.can_message ? 'сообщение не выполнено, отчёт прежний: ' : 'сообщение не выполнено: ';
        deepTail.appendChild(deepLogNote('dk-line note warn', said + deepClip(lastError, 400)));
      }
    }
    const work = el('div', 'dk-working');
    work.appendChild(el('span', 'dk-live-dot'));
    work.appendChild(el('span', 'dk-working-text', 'агент работает…'));
    deepTail.appendChild(work);
    if (pv || wasBottom) deepScrollDown();
  }
  const working = deepTail.querySelector('.dk-working');
  if (working) {
    const active = run && deepIsActive(run.status);
    working.style.display = active ? '' : 'none';
    if (active) working.lastChild.textContent = (run.status === 'queued' ? 'в очереди…' : 'агент работает…') + (num(run.elapsed) !== null ? ' ' + fmtDur(run.elapsed) : '');
  }
}

// Send a decision from a card: the controls are off meanwhile, a refusal brings them back.
async function deepSubmitDecision(card, body, setMsg, describeError) {
  const controls = [...card.querySelectorAll('button, input, textarea')].filter(c => !c.disabled);
  controls.forEach(c => { c.disabled = true; });
  setMsg('отправляю…');
  try {
    await sendJson(`/deep/runs/${encodeURIComponent(deep.runId)}/resume`, body);
    setMsg('отправлено, агент продолжает', 'ok');
    card.classList.add('sent');
    deepStartPolling();
    deepRefreshRuns();
  } catch (e) {
    controls.forEach(c => { c.disabled = false; });
    setMsg(describeError ? describeError(e) : e.message, 'err');
  }
}

// The budget stop: spent / limit, +$1, +$5, an amount of your own, finish with what is left.
// The click is the decision about money, so a live run needs no separate confirmation here.
function deepBudgetCard(pv, run) {
  const card = el('div', 'dk-card review');
  const head = el('div', 'dk-card-head');
  head.appendChild(el('span', 'dk-card-title', pv.title));
  head.appendChild(el('span', 'dk-card-meta', deepTimeOf(pv.askedAt)));
  card.appendChild(head);
  pv.summary.forEach(s => card.appendChild(el('div', 'dk-card-line', s)));
  const args = (deepPendingActions(pv.raw)[0] || {}).args || {};
  const msg = el('span', 'dk-card-msg');
  const setMsg = (t, cls) => { msg.textContent = t || ''; msg.className = 'dk-card-msg' + (cls ? ' ' + cls : ''); };
  const send = async (decision, payload) => {
    const res = deepResumeBody(decision, {payload, live: !!(run && run.live), confirm: true, allowed: pv.allowed});
    if (res.error) { setMsg(res.error, 'err'); return; }
    await deepSubmitDecision(card, res.body, setMsg);
  };
  const addMoney = (value) => {
    const a = deepParseBudgetAdd(value, args.day_left_usd);
    if (a.error) setMsg(a.error, 'err'); else send('edit', {add_usd: a.add});
  };
  const actions = el('div', 'dk-card-actions');
  [1, 5].forEach(x => {
    const b = deepBtn('+$' + x, 'primary', () => addMoney(x));
    if (deepParseBudgetAdd(x, args.day_left_usd).error) b.disabled = true;
    actions.appendChild(b);
  });
  const own = el('label', 'dk-money');
  own.appendChild(document.createTextNode('$'));
  const input = document.createElement('input');
  input.type = 'number';
  input.min = '0.01';
  input.step = '0.5';
  input.setAttribute('aria-label', 'сумма, которую добавить, $');
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addMoney(input.value); } });
  own.appendChild(input);
  actions.appendChild(own);
  actions.appendChild(deepBtn('Добавить', null, () => addMoney(input.value)));
  actions.appendChild(deepBtn('Завершить с тем, что есть', 'ghost', () => send('approve')));
  card.appendChild(actions);
  card.appendChild(msg);
  return card;
}

// Inline review card: title, ≤ 4 summary lines, Принять / Изменить / Отклонить, "Принять всё до конца".
function deepReviewCard(pv, run) {
  const card = el('div', 'dk-card review');
  const head = el('div', 'dk-card-head');
  head.appendChild(el('span', 'dk-card-title', pv.title));
  head.appendChild(el('span', 'dk-card-meta', deepTimeOf(pv.askedAt)));
  card.appendChild(head);
  pv.summary.forEach(s => card.appendChild(el('div', 'dk-card-line', s)));
  if (pv.detailsMd) {
    const det = el('details', 'dk-more');
    det.appendChild(el('summary', null, 'подробнее'));
    const body = el('div', 'md-render dk-more-body');
    det.appendChild(body);
    let rendered = false;
    det.addEventListener('toggle', () => {
      if (det.open && !rendered) { rendered = true; deepRenderMarkdownInto(body, pv.detailsMd); }
    });
    card.appendChild(det);
  }
  const live = !!(run && run.live);
  const zone = el('div', 'dk-card-zone');        // the inline editor / the reject comment
  const actions = el('div', 'dk-card-actions');
  const msg = el('span', 'dk-card-msg');
  let confirm = null;
  const btns = [];
  const setMsg = (t, cls) => { msg.textContent = t || ''; msg.className = 'dk-card-msg' + (cls ? ' ' + cls : ''); };
  const send = async (decision, opts) => {
    const o = opts || {};
    const res = deepResumeBody(decision, {payload: o.payload, comment: o.comment, approveRest: o.approveRest, live,
      confirm: confirm ? confirm.checked : false, allowed: pv.allowed});
    if (res.error) { setMsg(res.error, 'err'); return; }
    await deepSubmitDecision(card, res.body, setMsg, (e) => (o.approveRest && e.status === 400 && /approve_rest/.test(e.message)
      ? 'сервер не поддерживает «принять всё до конца»: принимайте по одной точке' : e.message));
  };
  const can = (x) => pv.allowed.includes(x);
  const showZone = (node) => { zone.textContent = ''; if (node) zone.appendChild(node); };
  if (can('approve')) btns.push(deepBtn('Принять', 'primary', () => send('approve')));
  if (can('edit')) {
    btns.push(deepBtn('Изменить', null, () => {
      if (zone.firstChild && zone.firstChild.dataset.kind === 'edit') { showZone(null); return; }
      showZone(deepEditor(pv, (payload, comment) => send('edit', {payload, comment}), () => showZone(null), setMsg));
    }));
  }
  if (can('reject')) {
    btns.push(deepBtn('Отклонить', 'danger', () => {
      if (zone.firstChild && zone.firstChild.dataset.kind === 'reject') { showZone(null); return; }
      const box = el('div', 'dk-reject');
      box.dataset.kind = 'reject';
      const ta = document.createElement('textarea');
      ta.rows = 2;
      ta.placeholder = 'Что не так? Агент продолжит с этим комментарием';
      box.appendChild(ta);
      const row = el('div', 'dk-card-actions');
      row.appendChild(deepBtn('Отклонить с комментарием', 'danger', () => send('reject', {comment: ta.value})));
      row.appendChild(deepBtn('Отмена', 'ghost', () => showZone(null)));
      box.appendChild(row);
      showZone(box);
      ta.focus();
    }));
  }
  btns.forEach(b => actions.appendChild(b));
  // "approve the rest": only a server that sends pending_point knows approve_rest
  if (can('approve') && !pv.legacy) {
    const rest = el('button', 'dk-linkbtn', 'Принять всё до конца');
    rest.type = 'button';
    rest.title = 'принять эту и все следующие остановки этого запуска';
    rest.addEventListener('click', () => send('approve', {approveRest: true}));
    actions.appendChild(rest);
  }
  if (live) {
    const lab = el('label', 'dk-confirm');
    confirm = document.createElement('input');
    confirm.type = 'checkbox';
    lab.appendChild(confirm);
    lab.appendChild(el('span', null, 'live: продолжение платное, подтверждаю'));
    confirm.addEventListener('change', () => lab.classList.toggle('armed', confirm.checked));
    actions.appendChild(lab);
  }
  card.appendChild(zone);
  card.appendChild(actions);
  card.appendChild(msg);
  return card;
}

// The inline editor of a stop: formalization (transcription, class, IR), report (fields), other (args JSON).
function deepEditor(pv, onSave, onCancel, setMsg) {
  const box = el('div', 'dk-editor');
  box.dataset.kind = 'edit';
  const p = pv.raw || {};
  const actions = deepPendingActions(p);
  const a = (actions[0] || {}).args || p.args || {};
  const allow = (k) => !pv.editable || !pv.editable.length || pv.editable.includes(k);
  const field = (label, input, hint) => {
    const f = el('label', 'dk-field');
    f.appendChild(el('span', 'dk-field-label', label));
    f.appendChild(input);
    if (hint) f.appendChild(el('span', 'dk-field-hint', hint));
    return f;
  };
  const ta = (value, rows, code) => {
    const t = document.createElement('textarea');
    t.value = value ?? '';
    t.rows = rows;
    t.spellcheck = false;
    if (code) t.className = 'code';
    return t;
  };
  const sel = (options, value) => {
    const s = document.createElement('select');
    options.forEach(o => { const [v, l] = Array.isArray(o) ? o : [o, o]; s.appendChild(new Option(l, v)); });
    s.value = value ?? '';
    return s;
  };
  const inputs = [];
  let getEdits;
  if (pv.point === 'formalization') {
    const tr = ta(a.transcription || a.task_text || '', 3);
    if (allow('transcription')) { box.appendChild(field('Транскрипция', tr)); inputs.push(['transcription', tr]); }
    const cls = sel(DEEP_CLASSES, a.class || '');
    const ir = ta(a.ir ? JSON.stringify(a.ir, null, 2) : '', 10, true);
    if (allow('class')) { box.appendChild(field('Класс', cls, a.valid === false ? 'validate_ir: IR некорректен' : '')); inputs.push(['class', cls]); }
    if (allow('ir')) { box.appendChild(field('IR (JSON)', ir)); inputs.push(['ir', ir]); }
    if (p.source_url) {
      const src = el('a', 'dk-linkbtn', 'исходник задачи ↗');
      src.href = p.source_url;
      src.target = '_blank';
      src.rel = 'noopener';
      box.appendChild(src);
    }
    getEdits = () => deepHitl1Edits(a, {
      transcription: allow('transcription') ? tr.value : undefined,
      irText: allow('ir') ? ir.value : undefined,
      cls: allow('class') ? cls.value : undefined,
    });
  } else if (pv.point === 'report') {
    const text = (key, label, rows) => {
      const t = rows ? ta(a[key] || '', rows) : document.createElement('input');
      if (!rows) { t.type = 'text'; t.value = a[key] || ''; t.spellcheck = false; }
      inputs.push([key, t]);
      return field(label, t);
    };
    const grid = el('div', 'dk-field-row');
    if (allow('template')) {
      const tpl = sel(deep.templates, a.template || 'full_proof');
      inputs.push(['template', tpl]);
      grid.appendChild(field('Шаблон', tpl));
    }
    if (allow('answer')) {
      const ans = sel([['', '—'], 'yes', 'no', 'inconclusive'], a.answer ?? '');
      inputs.push(['answer', ans]);
      grid.appendChild(field('Ответ', ans));
    }
    if (allow('language_class')) {
      const lc = sel(DEEP_CLASSES, a.language_class || '');
      inputs.push(['language_class', lc]);
      grid.appendChild(field('Класс', lc));
    }
    if (grid.childNodes.length) box.appendChild(grid);
    if (allow('verdict')) box.appendChild(text('verdict', 'Вердикт'));
    if (allow('method')) box.appendChild(text('method', 'Метод'));
    if (allow('summary')) box.appendChild(text('summary', 'Кратко', 3));
    const more = el('details', 'dk-more');
    more.appendChild(el('summary', null, 'гипотеза, построение, вывод, шаги'));
    const mb = el('div', 'dk-editor');
    if (allow('hypothesis')) mb.appendChild(text('hypothesis', 'Гипотеза', 2));
    if (allow('construction')) mb.appendChild(text('construction', 'Построение', 3));
    if (allow('conclusion')) mb.appendChild(text('conclusion', 'Вывод', 2));
    let steps = null;
    if (allow('steps')) {
      steps = ta(JSON.stringify(a.steps || [], null, 2), 10, true);
      mb.appendChild(field('Шаги (JSON)', steps, 'утверждения и id доказательств; trust шагов пересчитывает код'));
    }
    if (mb.childNodes.length) { more.appendChild(mb); box.appendChild(more); }
    getEdits = () => {
      const form = {};
      inputs.forEach(([k, n]) => { form[k] = n.value; });
      if (steps) form.stepsText = steps.value;
      return deepReportEdits(a, form);
    };
  } else {
    if (actions.length > 1) box.appendChild(el('div', 'dk-field-hint', `${actions.length} действия в этой остановке: изменить можно только одно`));
    const argsBox = ta(JSON.stringify(a || {}, null, 2), 8, true);
    box.appendChild(field(`${(actions[0] || {}).name || 'действие'}: аргументы`, argsBox));
    inputs.push(['args', argsBox]);
    getEdits = () => deepArgsEdits(a, argsBox.value);
  }
  const comment = document.createElement('input');
  comment.type = 'text';
  comment.placeholder = 'комментарий для агента (необязательно)';
  box.appendChild(comment);
  const row = el('div', 'dk-card-actions');
  const save = deepBtn('Сохранить и продолжить', 'primary', () => {
    const e = getEdits();
    if (e.error) { setMsg(e.error, 'err'); return; }
    onSave(e.payload, comment.value);
  });
  row.appendChild(save);
  row.appendChild(deepBtn('Отмена', 'ghost', onCancel));
  const changedEl = el('span', 'dk-field-hint', '');
  row.appendChild(changedEl);
  box.appendChild(row);
  const update = () => {
    const e = getEdits();
    changedEl.textContent = e.error ? e.error : (e.changed && e.changed.length ? 'изменено: ' + e.changed.join(', ') : 'ничего не изменено');
    changedEl.classList.toggle('err', !!e.error);
  };
  inputs.forEach(([, n]) => { n.addEventListener('input', update); n.addEventListener('change', update); });
  update();
  return box;
}

//---------------------------------------------------------------------------
// Right panel: the report document (or its draft), the log
//---------------------------------------------------------------------------
// What the panel shows: {url, draft, urls, template, canRender} or null (no report yet).
function deepPanelSource() {
  const d = deep.detail;
  if (!d || !d.run) return null;
  const res = d.result;
  const pv = deepPendingView(d);
  if (pv && pv.point === 'report') {
    const urls = Object.assign({}, (res && res.urls) || {}, (pv.raw && pv.raw.draft_urls) || {});
    if (!urls.html && !urls.md) return null;
    return {urls, draft: true, template: res ? deepReportTemplate(res, null) : '', canRender: false};
  }
  if (!res || res.draft) return null;
  const urls = res.urls || {};
  if (!urls.html && !urls.md) return null;
  return {urls, draft: false, template: deepReportTemplate(res, null), canRender: !!res.can_render, views: res.views || {},
    reportRevision: res.report_revision};
}
function deepPanelVisible() { return deepView.classList.contains('has-panel'); }
function deepShowPanel(view) {
  if (view === null) { deep.reportClosed = true; deep.panelView = 'report'; }
  else { deep.reportClosed = false; deep.panelView = view; }
  deep.panelKey = null;
  deepRenderPanel();
  deepRenderHead();
}

function deepRenderPanel() {
  const src = deepPanelSource();
  const wantLog = deep.panelView === 'log' && !!deep.runId;
  const show = !deep.reportClosed && (wantLog || !!src);
  deepView.classList.toggle('has-panel', show);
  deepView.classList.toggle('panel-wide', show && deep.reportWide);
  if (!show) { deep.panelKey = null; return; }
  const viewing = src && !src.draft && deep.viewTpl && deep.viewTpl.runId === deep.runId ? deep.viewTpl : null;
  const view = wantLog ? 'log' : 'report';
  // the same report object keeps its panel: a follow-up swaps its URLs to a snapshot and back, a text reply re-renders the files
  const what = src && !src.draft && src.reportRevision !== null && src.reportRevision !== undefined
    ? 'r' + src.reportRevision : Object.values(src ? src.urls : {}).join(',');
  const fu = deep.detail && deep.detail.followup;
  const controls = src && (src.canRender || (fu && fu.in_progress)) ? 'tpl' : '';
  const key = view === 'log' ? `${deep.runId}:log:${(deep.detail && deep.detail.log_tail || []).length}`
    : `${deep.runId}:${src.draft}:${what}:${src.template}:${viewing ? viewing.template : ''}:${controls}`;
  if (key === deep.panelKey) return;
  deep.panelKey = key;
  $('deep-panel-title').textContent = view === 'log' ? 'Журнал' : (src.draft ? 'Черновик отчёта' : 'Отчёт');
  // template control: a finished, accepted report can be viewed in another template
  const tplBox = $('deep-panel-tpl');
  tplBox.textContent = '';
  if (view === 'report' && src.template) {
    if (src.canRender) {
      const s = document.createElement('select');
      s.className = 'dk-select';
      deep.templates.forEach(t => s.appendChild(new Option(t, t)));
      s.value = viewing ? viewing.template : src.template;
      s.title = 'посмотреть отчёт в другом шаблоне (рендерит код из result.json; result.* сохраняет шаблон ' + src.template + ')';
      s.addEventListener('change', () => deepViewTemplate(s.value, src));
      tplBox.appendChild(s);
    } else {
      tplBox.appendChild(el('span', 'dk-tag', src.template));
    }
  }
  const links = $('deep-panel-links');
  links.textContent = '';
  const urls = view === 'log' ? {'run.log': deepFileUrl('run.log'), 'progress.jsonl': deepFileUrl('progress.jsonl')}
    : Object.assign({}, src.urls, viewing ? viewing.urls : {});
  Object.entries(urls).forEach(([s, u]) => {
    const a = el('a', 'dk-linkbtn', s + ' ↗');
    a.href = u;
    a.target = '_blank';
    a.rel = 'noopener';
    links.appendChild(a);
  });
  $('deep-panel-log').classList.toggle('active', view === 'log');
  $('deep-panel-log').textContent = view === 'log' ? 'отчёт' : 'журнал';
  $('deep-panel-log').style.display = view === 'log' && !src ? 'none' : '';
  deepReportBody.textContent = '';
  deepLogEl.style.display = view === 'log' ? '' : 'none';
  deepReportBody.style.display = view === 'log' ? 'none' : '';
  if (view === 'log') {
    const lines = deep.detail && Array.isArray(deep.detail.log_tail) ? deep.detail.log_tail : [];
    deepLogEl.textContent = lines.length ? lines.join('\n') + `\n\n(последние ${lines.length} строк; весь журнал — run.log)` : 'вывода пока нет';
    return;
  }
  if (src.draft) deepReportBody.appendChild(el('div', 'dk-panel-note', 'черновик: ждёт вашего решения в чате'));
  if (viewing) deepReportBody.appendChild(el('div', 'dk-panel-note', `вид в шаблоне ${viewing.template}; отчёт принят как ${src.template}`));
  const shown = Object.assign({}, src.urls, viewing ? viewing.urls : {});
  if (shown.html) {
    const f = document.createElement('iframe');
    f.className = 'dk-report-frame';
    f.setAttribute('sandbox', 'allow-scripts');   // the report's own JS runs; no same-origin access
    f.title = 'отчёт';
    f.src = shown.html;
    deepReportBody.appendChild(f);
  } else if (shown.md) {
    const box = el('div', 'md-render dk-report-md', 'загрузка…');
    fetch(shown.md, {cache: 'no-store'}).then(r => r.text()).then(t => deepRenderMarkdownInto(box, t))
      .catch(() => { box.textContent = 'не удалось загрузить result.md'; });
    deepReportBody.appendChild(box);
  }
}

async function deepViewTemplate(t, src) {
  const id = deep.runId;
  if (t === src.template) { deep.viewTpl = null; deep.panelKey = null; deepRenderPanel(); return; }
  if (src.views && src.views[t]) {
    deep.viewTpl = {runId: id, template: t, urls: src.views[t]};
  } else {
    try {
      const r = await sendJson(`/deep/runs/${encodeURIComponent(id)}/render`, {template: t});
      if (id !== deep.runId) return;
      deep.viewTpl = {runId: id, template: t, urls: r.urls || {}};
    } catch (e) {
      deepMsg(e.message, 'err');
    }
  }
  deep.panelKey = null;
  deepRenderPanel();
}
$('deep-panel-close').addEventListener('click', () => deepShowPanel(null));
$('deep-panel-wide').addEventListener('click', () => {
  deep.reportWide = !deep.reportWide;
  deepView.classList.toggle('panel-wide', deep.reportWide && deepPanelVisible());
  $('deep-panel-wide').textContent = deep.reportWide ? '⤡' : '⤢';
});
$('deep-panel-log').addEventListener('click', () => deepShowPanel(deep.panelView === 'log' ? 'report' : 'log'));

// Markdown -> sanitized HTML (the same marked + DOMPurify + KaTeX path as the pipelines' Markdown tab)
async function deepRenderMarkdownInto(target, rawMd) {
  if (!rawMd || !rawMd.trim()) { target.textContent = '—'; return; }
  try {
    await ensureMdLibs();
  } catch (e) {
    target.textContent = '';
    target.appendChild(el('pre', 'dk-pre', rawMd));
    return;
  }
  window.marked.setOptions({gfm: true, breaks: false, headerIds: false, mangle: false});
  // agent-generated text: always sanitized before innerHTML
  const tmp = document.createElement('div');
  tmp.innerHTML = DOMPurify.sanitize(window.marked.parse(preprocessObsidianCallouts(rawMd)));
  tmp.querySelectorAll('.callout-body[data-md]').forEach(n => {
    n.innerHTML = DOMPurify.sanitize(window.marked.parse(n.textContent));
    n.removeAttribute('data-md');
  });
  target.innerHTML = DOMPurify.sanitize(tmp.innerHTML);
  if (window.renderMathInElement) {
    window.renderMathInElement(target, {
      delimiters: [
        {left: '$$', right: '$$', display: true},
        {left: '$', right: '$', display: false},
        {left: '\\[', right: '\\]', display: true},
        {left: '\\(', right: '\\)', display: false},
      ],
      throwOnError: false,
    });
  }
}

//---------------------------------------------------------------------------
// Deep-agent settings (GET / PUT /api/deep/settings), opened by ⚙ in the runs list
//---------------------------------------------------------------------------
const DEEP_SET_NUMBERS = {
  budget: [['per_task_usd', 'на задачу, $', 0.01], ['per_day_usd', 'на день, $', 0.01],
    ['per_subagent_max_usd', 'на субагента, не больше $', 0.01], ['reserve_for_report_usd', 'резерв на отчёт, $', 0.01]],
  limits: [['max_model_calls', 'вызовов модели', 1], ['max_tool_calls', 'вызовов инструментов', 1],
    ['max_parallel', 'параллельно', 1], ['max_spawn_depth', 'глубина субагентов', 1]],
  intake: [['max_pdf_pages', 'страниц PDF', 1], ['max_file_mb', 'размер файла, MB', 0.5]],
};
const DEEP_KNOWN_MODELS = ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5'];
let deepSetForm = null;

function deepCloseSettings() { deepSettingsBack.classList.remove('show'); }
async function deepOpenSettings() {
  deepSettingsBack.classList.add('show');
  deepSettingsStatus.textContent = '';
  deepSettingsStatus.classList.remove('err');
  deepSettingsBody.textContent = '';
  deepSettingsBody.appendChild(el('span', 'hint', 'загрузка…'));
  try {
    const d = await api('/deep/settings');
    deepBuildSettingsForm(d);
  } catch (e) {
    deepSettingsBody.textContent = '';
    deepSettingsBody.appendChild(el('span', 'hint', 'не удалось загрузить настройки — ' + e.message));
  }
}
function deepBuildSettingsForm(d) {
  const s = d.settings || {};
  deepSettingsBody.textContent = '';
  const form = {numbers: [], allowed: [], roles: [], flags: []};
  deepSetForm = form;
  if (d.error) deepSettingsBody.appendChild(el('div', 'dk-panel-note err', 'файл настроек некорректен (показаны значения по умолчанию): ' + d.error));
  deepSettingsBody.appendChild(el('span', 'hint', `файл: ${d.path || '?'} · денежные лимиты действуют на live-запуски; mock ничего не тратит`));
  const group = (title) => {
    const g = el('div', 'dset-group');
    g.appendChild(el('div', 'dset-title', title));
    const grid = el('div', 'dset-grid');
    g.appendChild(grid);
    deepSettingsBody.appendChild(g);
    return grid;
  };
  const numField = (grid, section, key, label, step) => {
    const f = el('div', 'field');
    const inp = document.createElement('input');
    inp.type = 'number';
    inp.step = String(step);
    inp.value = (s[section] || {})[key] ?? '';
    inp.id = `dset-${section}-${key}`;
    const lab = el('label', null, label);
    lab.htmlFor = inp.id;
    f.appendChild(lab);
    f.appendChild(inp);
    grid.appendChild(f);
    form.numbers.push({section, key, inp, int: step === 1});
  };
  const flag = (grid, section, key, label) => {
    const lab = el('label', 'check');
    const inp = document.createElement('input');
    inp.type = 'checkbox';
    inp.checked = !!(s[section] || {})[key];
    lab.appendChild(inp);
    lab.appendChild(el('span', null, label));
    grid.appendChild(lab);
    form.flags.push({section, key, inp});
  };
  const gb = group('бюджет');
  DEEP_SET_NUMBERS.budget.forEach(([k, l, st]) => numField(gb, 'budget', k, l, st));
  const models = s.models || {};
  const gm = group('модели');
  const allowedNow = Array.isArray(models.allowed) ? models.allowed : [];
  const all = Array.from(new Set([...DEEP_KNOWN_MODELS, ...allowedNow, ...Object.values(models.roles || {})]));
  all.forEach(m => {
    const lab = el('label', 'check');
    const inp = document.createElement('input');
    inp.type = 'checkbox';
    inp.checked = allowedNow.includes(m);
    lab.appendChild(inp);
    lab.appendChild(el('span', null, 'разрешена: ' + m));
    gm.appendChild(lab);
    form.allowed.push({model: m, inp});
  });
  Object.entries(models.roles || {}).forEach(([role, m]) => {
    const f = el('div', 'field');
    const sel = document.createElement('select');
    all.forEach(x => sel.appendChild(new Option(x, x)));
    sel.value = m;
    sel.id = 'dset-role-' + role;
    const lab = el('label', null, 'роль ' + role);
    lab.htmlFor = sel.id;
    f.appendChild(lab);
    f.appendChild(sel);
    gm.appendChild(f);
    form.roles.push({role, sel});
  });
  flag(gm, 'models', 'opus_needs_reason', 'Opus — только с причиной (в журнал)');
  const gh = group('точки проверки (HITL)');
  flag(gh, 'hitl', 'formalization', 'формализация: транскрипция + IR');
  flag(gh, 'hitl', 'report', 'черновик отчёта');
  flag(gh, 'hitl', 'execute', 'каждая команда в песочнице');
  const gl = group('лимиты');
  DEEP_SET_NUMBERS.limits.forEach(([k, l, st]) => numField(gl, 'limits', k, l, st));
  const gi = group('приём файлов');
  DEEP_SET_NUMBERS.intake.forEach(([k, l, st]) => numField(gi, 'intake', k, l, st));
}
async function deepSaveSettings() {
  const form = deepSetForm;
  if (!form) return;
  const out = {budget: {}, models: {}, limits: {}, hitl: {}, intake: {}};
  for (const n of form.numbers) {
    const v = Number(n.inp.value);
    if (n.inp.value === '' || !Number.isFinite(v)) {
      deepSettingsStatus.textContent = `${n.section}.${n.key}: нужно число`;
      deepSettingsStatus.classList.add('err');
      return;
    }
    out[n.section][n.key] = n.int ? Math.round(v) : v;
  }
  out.models.allowed = form.allowed.filter(a => a.inp.checked).map(a => a.model);
  out.models.roles = {};
  form.roles.forEach(r => { out.models.roles[r.role] = r.sel.value; });
  form.flags.forEach(f => { out[f.section][f.key] = f.inp.checked; });
  deepSettingsStatus.classList.remove('err');
  deepSettingsStatus.textContent = 'сохраняю…';
  try {
    const d = await sendJson('/deep/settings', out, 'PUT');
    deepBuildSettingsForm(d);
    deepSettingsStatus.textContent = 'сохранено';
    deep.settings = d.settings || deep.settings;
    if (Array.isArray(d.templates) && d.templates.length) deep.templates = d.templates;
    deepFillControls();
    deepSyncControls();
    deepRefreshEstimate();
  } catch (e) {
    deepSettingsStatus.textContent = e.message;
    deepSettingsStatus.classList.add('err');
  }
}
$('deep-settings-open').addEventListener('click', deepOpenSettings);
$('deep-settings-close').addEventListener('click', deepCloseSettings);
$('deep-settings-cancel').addEventListener('click', deepCloseSettings);
$('deep-settings-save').addEventListener('click', deepSaveSettings);
deepSettingsBack.addEventListener('click', (e) => { if (e.target === deepSettingsBack) deepCloseSettings(); });

// the elapsed clock of the "working" line
setInterval(() => {
  if (document.hidden || !isDeepMode() || !deep.detail || !deep.detail.run || !deepIsActive(deep.detail.run.status)) return;
  deepRenderTail();
}, 1000);

//---------------------------------------------------------------------------
// Bootstrap: the tab chosen last (or #deep in the URL)
//---------------------------------------------------------------------------
(function initMode() {
  let mode = location.hash === '#deep' ? 'deep' : null;
  if (!mode) { try { mode = localStorage.getItem('tfl-lab-mode'); } catch (e) { mode = null; } }
  deepRenderAll();
  if (mode === 'deep') setMode('deep');
})();
