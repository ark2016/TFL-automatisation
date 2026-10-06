//---------------------------------------------------------------------------
// App state
//---------------------------------------------------------------------------
const state = {
  projects: [],
  project: null,
  examples: [],
  exampleName: null,
  runId: null,
  running: false,      // the run currently shown is active (queued/running/formalizing)
  pollTimer: null,     // setTimeout handle of the run poller
  pollGen: 0,          // bumped on every run switch so stale ticks drop out
  cursor: 0,           // progress.jsonl cursor (?after=N)
  events: [],          // progress events of the shown run
  run: null,           // merged run metadata of the shown run (status, usage, urls, ...)
  runs: [],            // last GET /api/runs listing
  runsTimer: null,
  side: 'examples',    // sidebar section: examples | runs
  settings: null,
  phaseOpen: {},       // phase key -> bool, set by the user toggling a section
  tickTimer: null,     // 1s timer refreshing the elapsed clock
  mdSource: '',        // raw markdown, re-rendered when libs load or tab opens
  mdLibsLoading: null, // promise while CDNs load
};

const API = '/api';

//---------------------------------------------------------------------------
// DOM refs
//---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const projSwitch  = $('proj-switch');
const exampleList = $('example-list');
const exampleCount = $('example-count');
const editor     = $('ir-editor');
const gutter     = $('gutter');
const stLines    = $('st-lines');
const stChars    = $('st-chars');
const stValid    = $('st-valid');
const editorTitle = $('editor-title');
const editorBadge = $('editor-project-badge');
const statusDot  = $('status-dot');
const statusText = $('status-text');
const btnMock    = $('btn-mock');
const btnLive    = $('btn-live');
const btnCancel  = $('btn-cancel');
const btnNew     = $('new-btn');
const confirmSpend = $('confirm-spend');
const liveGroup  = $('live-group');
const verbose    = $('verbose');
const resultIframe = $('result-iframe');
const htmlEmpty  = $('html-empty');
const jsonPanel  = document.querySelector('[data-panel="json"] pre');
const mdPanel    = document.getElementById('md-render');
const mdRawPanel = document.getElementById('md-raw');
const logPre     = $('log-pre');
const logBadge   = $('log-badge');
const dlHtml     = $('dl-html');
const dlJson     = $('dl-json');
const runList    = $('run-list');
const sideTabEx  = $('side-tab-examples');
const sideTabRuns = $('side-tab-runs');
const runHead    = $('run-head');
const rhStatus   = $('rh-status');
const rhId       = $('rh-id');
const rhProject  = $('rh-project');
const rhVerdict  = $('rh-verdict');
const rhCost     = $('rh-cost');
const rhUsage    = $('rh-usage');
const rhElapsed  = $('rh-elapsed');
const btnFormalize = $('btn-formalize');
const estLive    = $('est-live');
const estFormalize = $('est-formalize');
const mockFormalizeWarn = $('formalize-mock-warning');
const progressRoot = $('progress-root');
const btnSettings = $('btn-settings');
const settingsBack = $('settings-back');

//---------------------------------------------------------------------------
// Editor: gutter + stats
//---------------------------------------------------------------------------
function updateGutter() {
  const lines = editor.value.split('\n').length;
  const nums = [];
  for (let i = 1; i <= Math.max(lines, 1); i++) nums.push(i);
  gutter.textContent = nums.join('\n');
  stLines.textContent = lines;
  stChars.textContent = editor.value.length;
  checkValid();
}
function syncGutterScroll() {
  gutter.scrollTop = editor.scrollTop;
}
function checkValid() {
  const text = editor.value.trim();
  if (!text) { stValid.textContent = '–'; stValid.style.color = ''; return; }
  try {
    JSON.parse(text);
    stValid.textContent = 'valid json';
    stValid.style.color = 'var(--ok)';
  } catch (e) {
    stValid.textContent = 'invalid json';
    stValid.style.color = 'var(--danger)';
  }
}
editor.addEventListener('input', updateGutter);
editor.addEventListener('scroll', syncGutterScroll);

//---------------------------------------------------------------------------
// Projects + examples
//---------------------------------------------------------------------------
async function loadProjects() {
  try {
    const r = await fetch(`${API}/projects`);
    const data = await r.json();
    state.projects = data.projects || [];
  } catch (e) {
    state.projects = [
      {id: 'agent_system', label: 'REG'},
      {id: 'cfl_system',   label: 'CFL'},
      {id: 'dcfl_system',  label: 'DCFL'},
      {id: 'll_system',    label: 'LL'},
    ];
    setStatus('error', 'backend offline — using fallback project list');
  }
  renderProjects();
  if (state.projects.length) pickProject(state.projects[0].id);
}

function renderProjects() {
  projSwitch.innerHTML = '';
  state.projects.forEach(p => {
    const b = document.createElement('button');
    b.className = 'proj-btn';
    b.textContent = p.label;
    b.dataset.id = p.id;
    b.addEventListener('click', () => pickProject(p.id));
    projSwitch.appendChild(b);
  });
}

async function pickProject(id) {
  state.project = id;
  state.exampleName = null;
  document.querySelectorAll('.proj-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.id === id));
  const p = state.projects.find(x => x.id === id);
  editorBadge.textContent = p ? p.label : id;
  if (state.side === 'examples') exampleCount.textContent = '…';
  refreshEstimate('run');
  exampleList.innerHTML = '<div class="example-empty">loading…</div>';
  try {
    const r = await fetch(`${API}/examples/${encodeURIComponent(id)}`);
    const data = await r.json();
    state.examples = data.examples || [];
  } catch (e) {
    state.examples = [];
  }
  renderExamples();
}

function renderExamples() {
  updateSideCount();
  if (!state.examples.length) {
    exampleList.innerHTML = '<div class="example-empty">no examples found</div>';
    return;
  }
  exampleList.innerHTML = '';
  state.examples.forEach(ex => {
    const d = document.createElement('div');
    d.className = 'example';
    d.textContent = ex;
    d.title = ex;
    d.addEventListener('click', () => pickExample(ex));
    exampleList.appendChild(d);
  });
}

async function pickExample(name) {
  state.exampleName = name;
  document.querySelectorAll('.example').forEach(el =>
    el.classList.toggle('active', el.textContent === name));
  try {
    const r = await fetch(`${API}/example/${encodeURIComponent(state.project)}/${encodeURIComponent(name)}`);
    const data = await r.json();
    editor.value = JSON.stringify(data.ir || data, null, 2);
    editorTitle.textContent = `editor · ${name}`;
    updateGutter();
    refreshEstimate('run');
  } catch (e) {
    setStatus('error', `failed to load ${name}: ${e.message}`);
  }
}

//---------------------------------------------------------------------------
// Small helpers
//---------------------------------------------------------------------------
// A run is "active" while the backend still works on it. Anything else
// (completed, error, cancelled, or a status of an older run) is final.
const ACTIVE = new Set(['queued', 'running', 'formalizing']);
const isActive = (st) => ACTIVE.has(st);
// The process status stays "completed" when the subprocess produced a result;
// the pipeline's own outcome is result_status. A result with status "failure"
// (e.g. invalid task_type) is shown as FAILED, not as a completed run.
const isFailedResult = (r) => !!r && r.status === 'completed' && r.result_status === 'failure';
const shownStatus = (r) => (isFailedResult(r) ? 'failed' : ((r && r.status) || ''));
const resultErrorText = (r) => (r && Array.isArray(r.result_errors) ? r.result_errors.join('; ') : '');
// Why Formalize cannot apply to this run (null = it can).
function formalizeBlockReason(r) {
  if (isFailedResult(r)) return 'the pipeline reported a failure for this run, there is nothing to formalize'
    + (resultErrorText(r) ? ` (${resultErrorText(r)})` : '');
  if (r && r.status === 'completed' && !r.verdict) return 'the run has no verdict, there is nothing to formalize';
  return null;
}
// Client-side hint: does the IR's task_type belong to the selected pipeline?
function taskTypeProblem(ir, projectId) {
  const proj = state.projects.find(x => x.id === projectId);
  const allowed = proj && Array.isArray(proj.task_types) ? proj.task_types : null;
  if (!allowed || !ir || typeof ir !== 'object' || !('task_type' in ir)) return null;
  if (allowed.includes(ir.task_type)) return null;
  const owners = state.projects.filter(x => x.id !== projectId && Array.isArray(x.task_types)
    && x.task_types.includes(ir.task_type)).map(x => x.label);
  return `task_type "${ir.task_type}" is not valid for the ${proj.label} pipeline`
    + (owners.length ? ` — it belongs to ${owners.join('/')}, switch the tab` : '')
    + `; expected one of: ${allowed.join(', ')}`;
}
let dispatching = false;   // POST /api/run in flight

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}
function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function firstNum(...vals) {
  for (const v of vals) { const n = num(v); if (n !== null) return n; }
  return null;
}
// Epoch seconds, epoch millis or ISO string -> millis (or null).
function toMs(v) {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number') return v < 1e12 ? v * 1000 : v;
  const t = Date.parse(v);
  return Number.isNaN(t) ? null : t;
}
function fmtUsd(v) {
  const n = num(v);
  if (n === null) return '—';
  return '$' + n.toFixed(n >= 1 ? 2 : 4);
}
function fmtTok(v) {
  const n = num(v);
  if (n === null) return '—';
  return n >= 10000 ? (n / 1000).toFixed(1) + 'k' : String(Math.round(n));
}
function fmtDur(sec) {
  const n = num(sec);
  if (n === null || n < 0) return '';
  if (n < 60) return n.toFixed(1) + 's';
  const m = Math.floor(n / 60);
  return m + 'm ' + String(Math.round(n - m * 60)).padStart(2, '0') + 's';
}
function fmtWhen(ms) {
  if (ms === null) return '';
  try {
    return new Date(ms).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  } catch (e) { return ''; }
}
const projectLabel = (id) => {
  const p = state.projects.find(x => x.id === id);
  return p ? p.label : (id || '—');
};

async function api(path, opts) {
  const r = await fetch(API + path, opts);
  const text = await r.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = {raw: text}; }
  if (!r.ok) {
    const msg = (data && (data.error || data.message)) || text || r.statusText;
    const err = new Error(`${r.status}: ${msg}`);
    err.status = r.status;
    throw err;
  }
  return data;
}
function sendJson(path, body, method) {
  return api(path, {
    method: method || 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
}

//---------------------------------------------------------------------------
// Live-run confirmation gating + run buttons
//---------------------------------------------------------------------------
function syncRunButtons() {
  const busy = state.running || dispatching;
  btnMock.disabled = busy;
  btnLive.disabled = busy || !confirmSpend.checked;
  liveGroup.classList.toggle('armed', confirmSpend.checked);
  btnCancel.style.display = state.running ? '' : 'none';
  updateFormalizeButton();
}
confirmSpend.addEventListener('change', syncRunButtons);

//---------------------------------------------------------------------------
// Run pipeline
//---------------------------------------------------------------------------
async function runPipeline(live) {
  const text = editor.value.trim();
  if (!text) {
    setStatus('error', 'editor is empty');
    return;
  }
  let ir;
  try { ir = JSON.parse(text); }
  catch (e) {
    setStatus('error', `invalid JSON: ${e.message}`);
    return;
  }
  const ttProblem = taskTypeProblem(ir, state.project);
  if (ttProblem) {
    setStatus('error', ttProblem);
    return;
  }
  dispatching = true;
  syncRunButtons();
  const label = live ? 'live run' : 'mock run';
  const t0 = performance.now();
  setStatus('running', `${label}: dispatching to backend…`);
  try {
    const data = await sendJson('/run', {
      project: state.project,
      ir, live: !!live, verbose: verbose.checked,
    });
    state.pendingLabel = label;
    openRun(data.run_id, {fresh: true, status: data.status || 'running', project: state.project, live: !!live});
    refreshRuns();
  } catch (e) {
    const dt = ((performance.now() - t0) / 1000).toFixed(1);
    setStatus('error', `${label} failed after ${dt}s — ${e.message}`);
  } finally {
    dispatching = false;
    syncRunButtons();
  }
}

async function cancelRun() {
  if (!state.runId) return;
  btnCancel.disabled = true;
  try {
    await sendJson(`/runs/${encodeURIComponent(state.runId)}/cancel`, {});
    // The next poll tick picks up the final "cancelled" status.
  } catch (e) { /* transient — polling still reflects the real status */ }
  finally { btnCancel.disabled = false; }
}

//---------------------------------------------------------------------------
// Opening a run + polling its progress
//---------------------------------------------------------------------------
function stopPolling() {
  state.pollGen++;
  if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
}

function openRun(id, opts) {
  opts = opts || {};
  stopPolling();
  state.runId = id;
  state.events = [];
  state.cursor = 0;
  state.phaseOpen = {};
  state.partialJson = '';
  state.resultLoaded = false;
  state.logLen = -1;
  if (!opts.fresh) state.pendingLabel = '';
  state.run = {
    run_id: id,
    status: opts.status || 'running',
    project: opts.project || null,
    live: opts.live,
  };
  state.running = isActive(state.run.status);
  clearResults(opts.fresh ? 'running…' : 'loading…');
  renderProgress();
  renderRunHead();
  syncRunButtons();
  highlightRunItem();
  if (opts.fresh) activateTab('progress');
  refreshEstimate('formalize');
  startPolling(!opts.fresh);
}

function startPolling(pickTab) {
  const gen = ++state.pollGen;
  if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
  let first = !!pickTab;
  const tick = async () => {
    if (gen !== state.pollGen) return;
    try { await pollOnce(gen, first); } catch (e) { /* transient */ }
    first = false;
    if (gen !== state.pollGen) return;
    if (state.run && isActive(state.run.status)) state.pollTimer = setTimeout(tick, 700);
    else state.pollTimer = null;
  };
  tick();
}

const RUN_KEYS = ['status', 'project', 'verdict', 'confidence', 'live', 'source_mode', 'error', 'elapsed',
  'formalize_error', 'started', 'finished', 'result_html_url', 'result_json_url', 'result_md_url',
  'result_status', 'result_errors'];

async function pollOnce(gen, pickTab) {
  const id = state.runId;
  const enc = encodeURIComponent(id);
  const [detailRes, logRes] = await Promise.allSettled([
    api(`/runs/${enc}?after=${state.cursor}`),
    api(`/log/${enc}`),
  ]);
  if (gen !== state.pollGen) return;
  const detail = detailRes.status === 'fulfilled' ? detailRes.value : null;
  const log = logRes.status === 'fulfilled' ? logRes.value : null;
  if (!detail && !log) return;

  const wasActive = isActive(state.run.status);
  const run = Object.assign({}, state.run);
  // The log endpoint only supplies status/urls when the detail endpoint is missing.
  for (const src of [log, detail && detail.run, detail]) {
    if (!src || typeof src !== 'object') continue;
    for (const k of RUN_KEYS) {
      if (src[k] !== undefined && src[k] !== null) run[k] = src[k];
    }
  }
  if (detail) {
    const evs = Array.isArray(detail.events) ? detail.events
      : (Array.isArray(detail.progress) ? detail.progress : []);
    if (evs.length) state.events = state.events.concat(evs);
    const nxt = firstNum(detail.next, detail.cursor, detail.next_after);
    state.cursor = nxt !== null ? nxt : state.cursor + evs.length;
    const usage = (evs.length && evs[evs.length - 1].usage) || detail.usage || (detail.run && detail.run.usage);
    if (usage) run.usage = usage;
    if (detail.partial_result && typeof detail.partial_result === 'object') {
      const pj = JSON.stringify(detail.partial_result, null, 2);
      if (pj !== state.partialJson) {
        state.partialJson = pj;
        if (isActive(run.status) || !state.resultLoaded) {
          jsonPanel.textContent = pj;
          jsonPanel.classList.remove('empty');
        }
      }
    }
    // The gate's verdict shows in the header as soon as its event arrives.
    for (const e of evs) {
      if (e.event === 'verdict' && e.payload && e.payload.verdict) run.liveVerdict = e.payload.verdict;
    }
  }
  state.run = run;
  const active = isActive(run.status);
  state.running = active;

  if (log && Array.isArray(log.lines) && log.lines.length !== state.logLen) {
    state.logLen = log.lines.length;
    renderLog(log.lines);
    logBadge.textContent = log.lines.length;
  }

  renderProgress();
  renderRunHead();
  syncRunButtons();
  updateStatusLine();

  if (!active) {
    if (wasActive || !state.resultLoaded) {
      state.resultLoaded = true;
      loadResult(run);
      refreshRuns();
      refreshEstimate('formalize');
      if (pickTab) activateTab(run.result_html_url ? 'html' : 'progress');
    }
  }
}

function updateStatusLine() {
  const r = state.run;
  if (!r) return;
  const cost = r.usage ? ` · ${fmtUsd(r.usage.estimated_cost_usd)}` : '';
  const label = state.pendingLabel ? state.pendingLabel + ': ' : '';
  if (isActive(r.status)) {
    const last = state.events.length ? state.events[state.events.length - 1] : null;
    const where = last && last.node ? ` · ${friendlyNode(last.node)}` : '';
    setStatus('running', `${label}run ${r.run_id} — ${r.status}${where}${cost}`);
  } else if (r.status === 'error') {
    setStatus('error', `run ${r.run_id} errored` + (r.error ? ` — ${r.error}` : '') + cost);
  } else if (r.status === 'cancelled') {
    setStatus('error', `run ${r.run_id} cancelled${cost}`);
  } else if (isFailedResult(r)) {
    setStatus('error', `run ${r.run_id} FAILED` + (resultErrorText(r) ? ` — ${resultErrorText(r)}` : '') + cost);
  } else if (r.status === 'completed') {
    setStatus('success', `done · ${runElapsedText(r) || '—'} · verdict: ${r.verdict || '—'}${cost}`);
  } else {
    setStatus(null, `run ${r.run_id} — ${r.status || 'unknown'}${cost}`);
  }
}

//---------------------------------------------------------------------------
// Run header (status, verdict, running cost) + Formalize button
//---------------------------------------------------------------------------
function runElapsedMs(r) {
  const el0 = num(r.elapsed);
  if (!isActive(r.status) && el0 !== null) return el0 * 1000;
  const t0 = toMs(r.started) ?? (state.events.length ? toMs(state.events[0].ts) : null);
  if (t0 === null) return el0 !== null ? el0 * 1000 : null;
  const lastEv = state.events.length ? toMs(state.events[state.events.length - 1].ts) : null;
  const t1 = isActive(r.status) ? Date.now() : (toMs(r.finished) ?? lastEv ?? t0);
  return Math.max(0, t1 - t0);
}
function runElapsedText(r) {
  const ms = runElapsedMs(r);
  return ms === null ? '' : fmtDur(ms / 1000);
}

function renderRunHead() {
  const r = state.run;
  if (!r) { runHead.classList.remove('show'); return; }
  runHead.classList.add('show');
  rhStatus.textContent = shownStatus(r) || '—';
  rhStatus.className = 'pill ' + shownStatus(r);
  rhStatus.title = isFailedResult(r) ? resultErrorText(r) : '';
  rhId.textContent = r.run_id || '—';
  rhProject.textContent = projectLabel(r.project);
  rhVerdict.textContent = (r.verdict || r.liveVerdict)
    ? String(r.verdict || r.liveVerdict) + (num(r.confidence) !== null ? ` (${num(r.confidence).toFixed(2)})` : '')
    : '—';
  const u = r.usage || {};
  rhCost.textContent = fmtUsd(u.estimated_cost_usd ?? 0);
  rhUsage.textContent = r.usage
    ? `${u.calls ?? 0} calls · ${fmtTok(u.input_tokens)} in / ${fmtTok(u.output_tokens)} out`
    : '';
  rhElapsed.textContent = runElapsedText(r);
}
// Keep the clock of an active run moving between polls.
setInterval(() => { if (state.running && state.run) rhElapsed.textContent = runElapsedText(state.run); }, 1000);

// Did the last recorded formalization end machine-checked?
function formalizationProved() {
  let last = null;
  for (const e of state.events) if (e.event === 'formalization') last = e.payload || {};
  return !!last && (last.proved === true || last.status === 'proved');
}

function updateFormalizeButton() {
  const r = state.run;
  const formalizing = !!r && r.status === 'formalizing';
  const proj = r ? state.projects.find(x => x.id === r.project) : null;
  const supported = !proj || proj.formalize !== false;   // e.g. LL has no Lean step
  const blocked = formalizeBlockReason(r);
  const can = !!r && r.status === 'completed' && !dispatching && supported && !blocked;
  const had = state.events.some(e => e.event === 'formalization');
  const proved = had && formalizationProved();
  btnFormalize.textContent = formalizing ? 'Formalizing…'
    : (proved ? 'Re-run (proved)' : (had ? 'Formalize again' : 'Formalize'));
  btnFormalize.disabled = !(can && confirmSpend.checked);
  btnFormalize.title = !supported ? 'formalization is not available for this project'
    : (blocked ? blocked : proved ? 'already machine-checked: a re-run costs money and the proof is kept unless the new attempt proves it too'
      : 'Lean formalization of the finished result; needs the API-spend confirmation');
  estFormalize.style.display = can || formalizing ? '' : 'none';
  // A mock-mode result: formalization always makes paid (live) calls.
  const mockSource = !!r && (r.source_mode === 'mock' || (r.source_mode === undefined && r.live === false));
  mockFormalizeWarn.style.display = mockSource && supported && (can || formalizing) ? '' : 'none';
}

async function requestFormalize() {
  const r = state.run;
  if (!r || r.status !== 'completed' || formalizeBlockReason(r) || !confirmSpend.checked) return;
  const force = formalizationProved();
  if (force && !window.confirm(
      'This result is already formalized and machine-checked.\n\n' +
      'Running the formalization again spends API budget; the existing proof is kept ' +
      'unless the new attempt proves it as well.\n\nRun it again?')) return;
  btnFormalize.disabled = true;
  try {
    const body = {confirm_spend: true};
    if (force) body.force = true;
    const d = await sendJson(`/runs/${encodeURIComponent(r.run_id)}/formalize`, body);
    state.run = Object.assign({}, state.run, {status: d.status || 'formalizing', error: null});
    state.running = isActive(state.run.status);
    state.phaseOpen.formalization = true;
    state.pendingLabel = 'formalize';
    renderRunHead();
    renderProgress();
    syncRunButtons();
    updateStatusLine();
    activateTab('progress');
    startPolling(false);
    refreshRuns();
  } catch (e) {
    setStatus('error', `formalize failed — ${e.message}`);
    updateFormalizeButton();
  }
}

//---------------------------------------------------------------------------
// Progress tab: events -> phases
//---------------------------------------------------------------------------
const PHASES = [
  {key: 'hypothesis',    title: 'Hypothesis & classification'},
  {key: 'specialists',   title: 'Specialists'},
  {key: 'oracle',        title: 'Oracle checks'},
  {key: 'verdict',       title: 'Gate & verdict'},
  {key: 'formalization', title: 'Formalization'},
];

function friendlyNode(node) {
  return String(node || '').replace(/_node$/, '').replace(/^run_/, '').replace(/_/g, ' ') || '—';
}
// llm_call events carry the AGENT name (not the graph node) as `node`: classify
// those by agent. Only used for a call that could not be attached to an open
// node row (see buildModel).
function phaseOfAgent(agent) {
  const n = String(agent || '').toLowerCase();
  if (/formaliz|lean/.test(n)) return 'formalization';
  if (/input_parser|classifier|hypothesis/.test(n)) return 'hypothesis';
  if (/proof_check|verify/.test(n)) return 'oracle';
  if (/reasoning|retry_planner|validator|summarizer|invert/.test(n)) return 'verdict';
  return 'specialists';   // every other agent is a specialist / builder of some system
}
function phaseOf(ev) {
  if (ev.event === 'formalization') return 'formalization';
  if (ev.event === 'verdict') return 'verdict';
  const p = ev.payload || {};
  if (ev.event === 'llm_call') return phaseOfAgent(p.agent || ev.node);
  const n = String(ev.node || p.agent || p.name || '').toLowerCase();
  if (/formaliz|lean/.test(n)) return 'formalization';
  if (/specialist/.test(n)) return 'specialists';
  if (/oracle|verify|proof_check|claim|closure/.test(n)) return 'oracle';
  if (/reasoning|gate|verdict|retry|invert|assemble|render|early_failure/.test(n)) return 'verdict';
  return 'hypothesis';
}
const USED_KEYS = new Set(['specialist', 'agent', 'name', 'label', 'id', 'model', 'input_tokens',
  'output_tokens', 'cost_usd', 'estimated_cost_usd', 'cost', 'elapsed_s', 'duration_s', 'duration',
  'usage', 'verdict', 'confidence']);
function subLabel(p) {
  return String(p.specialist || p.agent || p.name || p.label || p.id || '');
}
function summarize(p, skip) {
  const parts = [];
  for (const [k, v] of Object.entries(p || {})) {
    if (USED_KEYS.has(k) || (skip && skip.has(k))) continue;
    let t;
    if (v === null || v === undefined || v === '') continue;
    if (Array.isArray(v)) t = `${v.length} item${v.length === 1 ? '' : 's'}`;
    else if (typeof v === 'object') t = JSON.stringify(v);
    else t = String(v);
    parts.push(`${k}=${t.length > 90 ? t.slice(0, 90) + '…' : t}`);
  }
  const out = parts.join(' · ');
  return out.length > 320 ? out.slice(0, 320) + '…' : out;
}

const PROGRESS_EVENTS = new Set(['node_start', 'node_done', 'llm_call', 'verdict', 'formalization']);
function buildModel(events) {
  const phases = {};
  PHASES.forEach(ph => { phases[ph.key] = {rows: [], boxes: []}; });
  let prevUsage = {calls: 0, input_tokens: 0, output_tokens: 0, estimated_cost_usd: 0};
  const errors = [];
  let lastActiveKey = null;
  const order = [];   // every row with its phase key, in creation order
  const newRow = (ev, p, kind) => ({
    node: ev.node || '', sub: subLabel(p), state: 'running', t0: toMs(ev.ts), t1: null,
    model: '', calls: 0, inTok: 0, outTok: 0, cost: 0, hasCost: false, detail: '',
    kind: kind || 'node',
  });
  const addRow = (key, row) => { phases[key].rows.push(row); order.push({row, key}); return row; };
  const findOpen = (ph, node, sub) => {
    for (let i = ph.rows.length - 1; i >= 0; i--) {
      const r = ph.rows[i];
      if (r.state === 'running' && r.kind === 'node' && r.node === node && r.sub === sub) return r;
    }
    return null;
  };
  // The row an llm_call belongs to: an open node row of the same agent (specialist
  // fan-out: node_start carries payload.agent), else an open row of that agent that
  // only the call's own "start" event created, else the innermost open node row
  // without an agent (a sequential node, e.g. run_reasoning_node).
  const findCallOwner = (agent) => {
    for (let i = order.length - 1; i >= 0; i--) {
      const o = order[i];
      if (o.row.state === 'running' && o.row.kind === 'node' && agent && o.row.sub === agent) return o;
    }
    for (let i = order.length - 1; i >= 0; i--) {
      const o = order[i];
      if (o.row.state === 'running' && o.row.kind === 'llm' && o.row.sub === agent) return o;
    }
    for (let i = order.length - 1; i >= 0; i--) {
      const o = order[i];
      if (o.row.state === 'running' && o.row.kind === 'node' && !o.row.sub) return o;
    }
    return null;
  };
  for (const ev of events) {
    const p = ev.payload || {};
    const u = ev.usage || null;
    // Per-event usage deltas from the cumulative counter (fallback when the payload has none).
    let dCalls = 0, dIn = 0, dOut = 0, dCost = 0;
    if (u) {
      dCalls = (num(u.calls) ?? 0) - prevUsage.calls;
      dIn = (num(u.input_tokens) ?? 0) - prevUsage.input_tokens;
      dOut = (num(u.output_tokens) ?? 0) - prevUsage.output_tokens;
      dCost = (num(u.estimated_cost_usd) ?? 0) - prevUsage.estimated_cost_usd;
      prevUsage = {
        calls: num(u.calls) ?? prevUsage.calls,
        input_tokens: num(u.input_tokens) ?? prevUsage.input_tokens,
        output_tokens: num(u.output_tokens) ?? prevUsage.output_tokens,
        estimated_cost_usd: num(u.estimated_cost_usd) ?? prevUsage.estimated_cost_usd,
      };
    }
    let key = phaseOf(ev);
    const sub = subLabel(p);
    switch (ev.event) {
      case 'node_start': {
        addRow(key, newRow(ev, p));
        break;
      }
      case 'node_done': {
        const ph = phases[key];
        let row = findOpen(ph, ev.node || '', sub);
        if (!row) { row = addRow(key, newRow(ev, p)); row.t0 = null; }
        row.state = p.error ? 'error' : 'done';
        row.t1 = toMs(ev.ts);
        row.detail = summarize(p);
        if (p.model && !row.model) row.model = String(p.model);
        break;
      }
      case 'llm_call': {
        const agent = String(p.agent || ev.node || '');
        const isStart = p.phase === 'start';
        let owner = findCallOwner(agent);
        if (!owner) {
          // No node row is open (e.g. the separate formalization loop, or a call
          // outside any instrumented node): the call gets a row of its own.
          const row = newRow(ev, {agent}, 'llm');
          row.node = agent;
          row.sub = agent;
          addRow(key, row);
          owner = order[order.length - 1];
        }
        key = owner.key;
        const row = owner.row;
        if (p.model) row.model = String(p.model);
        if (isStart) break;     // a start event only marks the step as running: counted on "done"
        row.calls += 1;
        row.inTok += firstNum(p.input_tokens, dIn) ?? 0;
        row.outTok += firstNum(p.output_tokens, dOut) ?? 0;
        const c = firstNum(p.cost_usd, p.estimated_cost_usd, p.cost, u ? dCost : null);
        if (c !== null) { row.cost += c; row.hasCost = true; }
        if (p.error) row.detail = String(p.error);
        if (row.kind === 'llm') { row.state = p.error ? 'error' : 'done'; row.t1 = toMs(ev.ts); }
        break;
      }
      case 'verdict': {
        phases[key].boxes.push({
          kind: 'verdict',
          verdict: p.verdict ?? p.value ?? '',
          confidence: num(p.confidence),
          detail: String(p.reason || p.rationale || p.explanation || p.message || p.gate || ''),
          extra: summarize(p, new Set(['reason', 'rationale', 'explanation', 'message', 'gate'])),
        });
        break;
      }
      case 'formalization': {
        const status = String(p.status || p.state || '').toLowerCase();
        const failed = !!p.error || /fail|error|give.?up/.test(status);
        const running = /start|running|attempt|progress|check/.test(status) && !failed;
        const row = newRow(ev, p, 'formalization');
        row.node = 'formalization';
        row.sub = status || sub || 'step';
        row.state = failed ? 'error' : (running ? 'running' : 'done');
        row.t0 = null;
        row.t1 = toMs(ev.ts);
        row.model = p.model ? String(p.model) : '';
        row.inTok = firstNum(p.input_tokens, dIn) ?? 0;
        row.outTok = firstNum(p.output_tokens, dOut) ?? 0;
        const c = firstNum(p.cost_usd, p.estimated_cost_usd, p.cost, u ? dCost : null);
        if (c !== null && c > 0) { row.cost = c; row.hasCost = true; }
        row.detail = summarize(p, new Set(['status', 'state']));
        addRow(key, row);
        break;
      }
      case 'error':
        errors.push(String(p.message || p.error || p.traceback || JSON.stringify(p)));
        break;
      default: break; // "done" and unknown events carry no section of their own
    }
    if (PROGRESS_EVENTS.has(ev.event)) lastActiveKey = key;
  }
  return {phases, errors, lastActiveKey};
}

function rowStatsText(row, nowMs) {
  const parts = [];
  const t1 = row.state === 'running' ? nowMs : row.t1;
  if (row.t0 !== null && t1 !== null && t1 !== undefined) parts.push(fmtDur((t1 - row.t0) / 1000));
  if (row.calls > 1) parts.push(`${row.calls} calls`);
  if (row.inTok || row.outTok) parts.push(`${fmtTok(row.inTok)}↑ ${fmtTok(row.outTok)}↓`);
  if (row.hasCost) parts.push(fmtUsd(row.cost));
  return parts.filter(Boolean).join(' · ');
}

function renderProgress() {
  const keepScroll = progressRoot.scrollTop;
  progressRoot.textContent = '';
  const r = state.run;
  if (!r) {
    progressRoot.appendChild(el('div', 'progress-empty', 'no run yet — start one, or open a run from the runs list'));
    return;
  }
  const {phases, errors, lastActiveKey} = buildModel(state.events);
  const active = isActive(r.status);
  const lastTs = state.events.length ? toMs(state.events[state.events.length - 1].ts) : null;
  const now = active ? Date.now() : (lastTs ?? Date.now());
  if (!active) {
    // A finished run has nothing in flight: steps that never reported "done" are shown as unfinished.
    PHASES.forEach(def => phases[def.key].rows.forEach(x => { if (x.state === 'running') x.state = 'stale'; }));
  }
  if (!state.events.length) {
    progressRoot.appendChild(el('div', 'progress-empty',
      active ? 'waiting for the first progress event…' : 'no progress events recorded for this run'));
  }
  PHASES.forEach(def => {
    const ph = phases[def.key];
    const has = ph.rows.length || ph.boxes.length;
    if (def.key === 'formalization' && !has && r.status !== 'formalizing') return;
    if (!has && !state.events.length) return;
    const anyRunning = ph.rows.some(x => x.state === 'running');
    const anyError = ph.rows.some(x => x.state === 'error');
    const stateCls = !has ? '' : (anyRunning ? 'running' : (anyError ? 'error' : 'done'));

    const d = el('details', 'phase' + (has ? '' : ' pending'));
    const defOpen = anyRunning || def.key === lastActiveKey || (!active && def.key === 'verdict' && has);
    d.open = state.phaseOpen[def.key] !== undefined ? state.phaseOpen[def.key] : defOpen;
    const sum = el('summary');
    sum.appendChild(el('span', 'ph-state ' + stateCls));
    sum.appendChild(el('span', 'ph-title', def.title));
    const cost = ph.rows.reduce((a, x) => a + (x.hasCost ? x.cost : 0), 0);
    const tok = ph.rows.reduce((a, x) => a + x.inTok + x.outTok, 0);
    const meta = [];
    if (ph.rows.length) meta.push(`${ph.rows.length} step${ph.rows.length === 1 ? '' : 's'}`);
    if (tok) meta.push(fmtTok(tok) + ' tok');
    if (cost) meta.push(fmtUsd(cost));
    sum.appendChild(el('span', 'ph-meta', meta.join(' · ')));
    sum.addEventListener('click', (e) => {
      e.preventDefault();
      d.open = !d.open;
      state.phaseOpen[def.key] = d.open;
    });
    d.appendChild(sum);

    const body = el('div', 'phase-body');
    ph.rows.forEach(row => {
      const line = el('div', 'pr-row ' + row.state);
      line.appendChild(el('span', 'pr-mark', row.state === 'done' ? '✓' : (row.state === 'error' ? '✗' : (row.state === 'stale' ? '○' : '●'))));
      const name = el('span', 'pr-name');
      const title = row.node === 'formalization'
        ? 'formalization' + (row.sub ? ' · ' + row.sub : '')
        : friendlyNode(row.node) + (row.sub ? ' · ' + row.sub : '');
      name.appendChild(document.createTextNode(title));
      if (row.model) name.appendChild(el('span', 'pr-model', row.model));
      line.appendChild(name);
      line.appendChild(el('span', 'pr-stats', rowStatsText(row, now)));
      if (row.detail) line.appendChild(el('span', 'pr-detail', row.detail));
      body.appendChild(line);
    });
    ph.boxes.forEach(b => {
      const box = el('div', 'pr-verdict');
      box.appendChild(document.createTextNode('verdict: ' + (b.verdict || '—')));
      if (b.confidence !== null) box.appendChild(el('span', 'pv-conf', 'confidence ' + b.confidence.toFixed(2)));
      const more = [b.detail, b.extra].filter(Boolean).join('\n');
      if (more) box.appendChild(el('div', 'pr-detail', more));
      body.appendChild(box);
    });
    if (!has) body.appendChild(el('div', 'progress-empty', 'not reached yet'));
    d.appendChild(body);
    progressRoot.appendChild(d);
  });
  const errTexts = errors.slice();
  if (r.status === 'error' && r.error && !errTexts.length) errTexts.push(String(r.error));
  if (isFailedResult(r) && !errTexts.length) {
    (Array.isArray(r.result_errors) && r.result_errors.length ? r.result_errors : ['the pipeline reported a failure'])
      .forEach(t => errTexts.push(String(t)));
  }
  if (r.formalize_error) errTexts.push('formalization: ' + r.formalize_error);
  errTexts.forEach(t => progressRoot.appendChild(el('div', 'pr-error', t)));
  progressRoot.scrollTop = keepScroll;
}

//---------------------------------------------------------------------------
// Result loading (final files of a finished run)
//---------------------------------------------------------------------------
function loadResult(data) {
  const id = state.runId;
  const same = () => state.runId === id;
  if (data.result_html_url) {
    htmlEmpty.style.display = 'none';
    resultIframe.style.display = 'block';
    resultIframe.src = 'about:blank';
    resultIframe.src = data.result_html_url;
    dlHtml.href = data.result_html_url;
    dlHtml.classList.remove('disabled');
  } else {
    htmlEmpty.style.display = 'flex';
    htmlEmpty.textContent = data.status === 'error' ? 'the run failed — no result was produced' : 'no result files for this run';
  }
  if (data.result_json_url) {
    dlJson.href = data.result_json_url;
    dlJson.classList.remove('disabled');
    fetch(data.result_json_url, {cache: 'no-store'}).then(r => r.text()).then(t => {
      if (!same()) return;
      try {
        jsonPanel.textContent = JSON.stringify(JSON.parse(t), null, 2);
      } catch { jsonPanel.textContent = t; }
      jsonPanel.classList.remove('empty');
    }).catch(() => {});
  }
  if (data.result_md_url) {
    fetch(data.result_md_url, {cache: 'no-store'}).then(r => r.text()).then(t => {
      if (!same()) return;
      state.mdSource = t;
      mdRawPanel.textContent = t;
      mdRawPanel.classList.remove('empty');
      renderMarkdown(t);
    }).catch(() => {});
  }
}

//---------------------------------------------------------------------------
// Runs list (sidebar)
//---------------------------------------------------------------------------
function setSide(which) {
  state.side = which;
  sideTabEx.classList.toggle('active', which === 'examples');
  sideTabRuns.classList.toggle('active', which === 'runs');
  exampleList.style.display = which === 'examples' ? '' : 'none';
  runList.style.display = which === 'runs' ? '' : 'none';
  updateSideCount();
  if (which === 'runs') refreshRuns();
}
function updateSideCount() {
  exampleCount.textContent = state.side === 'runs' ? state.runs.length : state.examples.length;
}
sideTabEx.addEventListener('click', () => setSide('examples'));
sideTabRuns.addEventListener('click', () => setSide('runs'));

let runsFirstLoad = true;
async function refreshRuns() {
  let d;
  try { d = await api('/runs'); } catch (e) { return; }
  const list = Array.isArray(d) ? d : (d.runs || []);
  state.runs = list.slice().sort((a, b) => (toMs(b.started ?? b.created) ?? 0) - (toMs(a.started ?? a.created) ?? 0));
  renderRuns();
  updateSideCount();
  if (runsFirstLoad) {
    runsFirstLoad = false;
    // After a page reload, re-attach to the newest run that is still going.
    const act = state.runs.find(x => isActive(x.status));
    if (act && !state.runId) openRun(act.run_id || act.id, {status: act.status, project: act.project, live: act.live});
  }
}
function renderRuns() {
  runList.textContent = '';
  if (!state.runs.length) {
    runList.appendChild(el('div', 'example-empty', 'no runs yet'));
    return;
  }
  state.runs.forEach(r => {
    const id = r.run_id || r.id;
    const item = el('div', 'run-item' + (id === state.runId ? ' active' : ''));
    item.dataset.id = id;
    item.appendChild(el('span', 'ri-dot ' + shownStatus(r)));
    item.appendChild(el('span', 'ri-title', `${projectLabel(r.project)} · ${id}`));
    const cost = firstNum(r.estimated_cost_usd, r.cost_usd, r.usage && r.usage.estimated_cost_usd);
    item.appendChild(el('span', 'ri-cost', cost === null ? '' : fmtUsd(cost)));
    const sub = [shownStatus(r), r.verdict, fmtWhen(toMs(r.started ?? r.created))].filter(Boolean).join(' · ');
    item.appendChild(el('span', 'ri-sub', sub));
    item.title = isFailedResult(r) && resultErrorText(r) ? `${sub}\n${resultErrorText(r)}` : sub;
    item.addEventListener('click', () => openRun(id, {status: r.status, project: r.project, live: r.live}));
    runList.appendChild(item);
  });
}
function highlightRunItem() {
  runList.querySelectorAll('.run-item').forEach(n =>
    n.classList.toggle('active', n.dataset.id === state.runId));
}
setInterval(() => {
  if (document.hidden) return;
  if (state.side === 'runs' || state.runs.some(x => isActive(x.status))) refreshRuns();
}, 3000);

//---------------------------------------------------------------------------
// Cost estimates (GET /api/estimate)
//---------------------------------------------------------------------------
const estSeq = {run: 0, formalize: 0};
// Run estimate: measured from the recorded live runs of the project (server side),
// or "no data" when there is none. Formalize estimate: computed from the finished
// result and the config prices, shown as min / expected / max.
function setEstimate(target, text, title, strong) {
  target.textContent = text;
  target.title = title || '';
  target.classList.toggle('strong', !!strong);
}
async function refreshEstimate(kind) {
  const target = kind === 'run' ? estLive : estFormalize;
  const seq = ++estSeq[kind];
  try {
    if (kind === 'run') {
      if (!state.project) return;
      const qs = new URLSearchParams({project: state.project});
      const d = await api('/estimate?' + qs.toString());
      if (seq !== estSeq.run) return;
      const r = (d && d.run) || {};
      const avg = num(r.estimated_cost_usd), lo = num(r.low_usd), hi = num(r.high_usd);
      let txt;
      if (avg === null) txt = 'est. — (no data yet)';
      else if (lo !== null && hi !== null && (r.n || 0) > 1) txt = `est. ~${fmtUsd(avg)} (${fmtUsd(lo)}–${fmtUsd(hi)}, ${r.n} runs)`;
      else txt = `est. ~${fmtUsd(avg)} (${r.n || 1} run)`;
      if (d && d.with_formalize) txt += ' + formalization';
      const title = [r.note, d && d.formalize_note].filter(Boolean).join(' — ');
      setEstimate(target, txt, title, (hi ?? avg ?? 0) >= 1);
    } else {
      if (!state.run || !state.run.run_id) { setEstimate(target, '', ''); return; }
      const proj = state.projects.find(x => x.id === state.run.project);
      if (proj && proj.formalize === false) { setEstimate(target, '', ''); return; }
      const d = await api(`/runs/${encodeURIComponent(state.run.run_id)}/formalize/estimate`);
      if (seq !== estSeq.formalize) return;
      if (d.formalizable === false) {
        setEstimate(target, 'not formalizable', d.reason || '');
        return;
      }
      const lo = num(d.min_usd), ex = num(d.expected_usd), hi = num(d.max_usd);
      const txt = `est. min ${fmtUsd(lo)} · exp. ${fmtUsd(ex)} · max ${fmtUsd(hi)}`;
      const s = d.settings || {};
      const title = [
        `min = first attempt only; expected = every allowed attempt at typical size; max = hard ceiling (every call, including the one body-only call, at max_tokens)`,
        s.first_model ? `${s.first_model} then ${s.retry_model} x${s.retries}, max_tokens ${s.max_tokens}` : '',
        d.assumptions || '',
      ].filter(Boolean).join('\n');
      setEstimate(target, txt, title, (hi ?? 0) >= 1);
    }
  } catch (err) {
    if (seq !== estSeq[kind]) return;
    setEstimate(target, 'est. n/a', String(err.message || err));
  }
}

//---------------------------------------------------------------------------
// Settings (GET/PUT /api/settings)
//---------------------------------------------------------------------------
const setFormalize = $('set-formalize');
const setFirst = $('set-first-model');
const setRetry = $('set-retry-model');
const setRetries = $('set-retries');
const setMaxTok = $('set-max-tokens');
const setStatusEl = $('settings-status');

function fillSettings(s) {
  setFormalize.checked = !!s.formalize_in_run;
  setFirst.value = s.first_model || '';
  setRetry.value = s.retry_model || '';
  setRetries.value = s.retries ?? 2;
  setMaxTok.value = s.max_tokens ?? 128000;
}
async function loadSettings() {
  setStatusEl.textContent = '';
  setStatusEl.classList.remove('err');
  try {
    const d = await api('/settings');
    state.settings = (d && typeof d.settings === 'object' && d.settings) ? d.settings : d;
    fillSettings(state.settings);
  } catch (e) {
    setStatusEl.textContent = 'could not load settings — ' + e.message;
    setStatusEl.classList.add('err');
  }
}
function openSettings() {
  settingsBack.classList.add('show');
  loadSettings();
  setFormalize.focus();
}
function closeSettings() { settingsBack.classList.remove('show'); }
async function saveSettings() {
  const retries = Math.round(Number(setRetries.value));
  const maxTok = Math.round(Number(setMaxTok.value));
  const fail = (msg) => { setStatusEl.textContent = msg; setStatusEl.classList.add('err'); };
  if (!setFirst.value.trim() || !setRetry.value.trim()) return fail('model names must not be empty');
  if (!Number.isFinite(retries) || retries < 0 || retries > 10) return fail('corrections: 0–10');
  if (!Number.isFinite(maxTok) || maxTok < 1024 || maxTok > 128000) return fail('max output tokens: 1024–128000');
  setStatusEl.classList.remove('err');
  setStatusEl.textContent = 'saving…';
  try {
    const d = await sendJson('/settings', {
      formalize_in_run: setFormalize.checked,
      first_model: setFirst.value.trim(),
      retry_model: setRetry.value.trim(),
      retries,
      max_tokens: maxTok,
    }, 'PUT');
    state.settings = (d && typeof d.settings === 'object' && d.settings) ? d.settings : (d && d.formalize_in_run !== undefined ? d : state.settings);
    if (state.settings) fillSettings(state.settings);
    setStatusEl.textContent = 'saved';
    refreshEstimate('run');
    refreshEstimate('formalize');
  } catch (e) {
    fail(e.message);
  }
}
btnSettings.addEventListener('click', openSettings);
$('settings-close').addEventListener('click', closeSettings);
$('settings-cancel').addEventListener('click', closeSettings);
$('settings-save').addEventListener('click', saveSettings);
settingsBack.addEventListener('click', (e) => { if (e.target === settingsBack) closeSettings(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSettings(); });

//---------------------------------------------------------------------------
// Md sub-tab toggle (Preview ↔ Raw)
//---------------------------------------------------------------------------
document.querySelectorAll('.md-subtab').forEach(btn => {
  btn.addEventListener('click', () => {
    const mode = btn.dataset.mode;
    document.querySelectorAll('.md-subtab').forEach(b =>
      b.classList.toggle('active', b === btn));
    if (mode === 'preview') {
      mdPanel.style.display = '';
      mdRawPanel.style.display = 'none';
    } else {
      mdPanel.style.display = 'none';
      mdRawPanel.style.display = '';
    }
  });
});

//---------------------------------------------------------------------------
// Markdown rendering (marked + DOMPurify + KaTeX, lazy-loaded from CDN)
//---------------------------------------------------------------------------
// Pinned to exact versions on cdnjs (allowed by the page's CSP script-src)
// with Subresource Integrity hashes, so a compromised or MITM'd CDN response
// is rejected by the browser instead of executing.
const CDN = 'https://cdnjs.cloudflare.com/ajax/libs';
const MD_LIBS = [
  {kind: 'script', src: `${CDN}/marked/12.0.2/marked.min.js`,
   integrity: 'sha384-/TQbtLCAerC3jgaim+N78RZSDYV7ryeoBCVqTuzRrFec2akfBkHS7ACQ3PQhvMVi'},
  {kind: 'script', src: `${CDN}/dompurify/3.4.16/purify.min.js`,
   integrity: 'sha384-a7SzOxErzJ3ZpQz0zJ32d67dSitNzPcbfybc/ykU9KJhMgZkwqfSxlhhdJRS+XGL'},
  {kind: 'script', src: `${CDN}/KaTeX/0.16.11/katex.min.js`,
   integrity: 'sha384-7zkQWkzuo3B5mTepMUcHkMB5jZaolc2xDwL6VFqjFALcbeS9Ggm/Yr2r3Dy4lfFg'},
  {kind: 'script', src: `${CDN}/KaTeX/0.16.11/contrib/auto-render.min.js`,
   integrity: 'sha384-43gviWU0YVjaDtb/GhzOouOXtZMP/7XUzwPTstBeZFe/+rCMvRwr4yROQP43s0Xk'},
  {kind: 'style', src: `${CDN}/KaTeX/0.16.11/katex.min.css`,
   integrity: 'sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+'},
];
function ensureMdLibs() {
  if (state.mdLibsLoading) return state.mdLibsLoading;
  state.mdLibsLoading = Promise.all(MD_LIBS.map(lib =>
    lib.kind === 'script' ? loadScript(lib.src, lib.integrity) : loadStyle(lib.src, lib.integrity)));
  return state.mdLibsLoading;
}
function loadScript(src, integrity) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src; s.async = true;
    if (integrity) { s.integrity = integrity; s.crossOrigin = 'anonymous'; }
    s.onload = resolve; s.onerror = () => reject(new Error('failed to load ' + src));
    document.head.appendChild(s);
  });
}
function loadStyle(href, integrity) {
  return new Promise((resolve) => {
    const l = document.createElement('link');
    l.rel = 'stylesheet'; l.href = href;
    if (integrity) { l.integrity = integrity; l.crossOrigin = 'anonymous'; }
    l.onload = resolve; l.onerror = resolve;  // don't block markdown rendering on CSS
    document.head.appendChild(l);
  });
}

// Pre-process Obsidian-style callouts:
//   > [!theorem] Вердикт: ...
//   > continuation line
//   > another line
// into real HTML <div class="callout callout-theorem"> blocks, since
// marked.js treats them as plain blockquotes. Run BEFORE marked.
function preprocessObsidianCallouts(md) {
  const lines = md.split(/\r?\n/);
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const m = line.match(/^>\s*\[!(\w+)\]\s*(.*)$/);
    if (m) {
      const kind = m[1].toLowerCase();
      const title = m[2].trim();
      const body = [];
      i++;
      while (i < lines.length && lines[i].startsWith('>')) {
        body.push(lines[i].replace(/^>\s?/, ''));
        i++;
      }
      const bodyMd = body.join('\n').trim();
      // Emit HTML block. marked (with `html: true` default) passes <div> through.
      out.push('<div class="callout callout-' + escapeAttr(kind) + '">');
      if (title) {
        out.push('<div class="callout-title">' + escapeHtmlPreserveMath(title) + '</div>');
      }
      if (bodyMd) {
        // Mark the body as a fence-less inline fragment that we'll render
        // with marked inline-mode after the outer pass.
        out.push('<div class="callout-body" data-md="1">');
        out.push(bodyMd);
        out.push('</div>');
      }
      out.push('</div>\n');
      continue;
    }
    out.push(line);
    i++;
  }
  return out.join('\n');
}

function escapeAttr(s) { return String(s).replace(/[^a-z0-9-_]/gi, ''); }
function escapeHtmlPreserveMath(s) {
  return String(s).replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
}

async function renderMarkdown(rawMd) {
  mdPanel.classList.remove('empty');
  if (!rawMd || !rawMd.trim()) {
    mdPanel.textContent = '—';
    mdPanel.classList.add('empty');
    return;
  }
  mdPanel.innerHTML = '<div style="color:var(--text-faint);font-family:var(--font-mono);font-size:11px">loading renderer…</div>';
  try {
    await ensureMdLibs();
  } catch (e) {
    // Fall back to plain <pre> if CDN unreachable
    mdPanel.innerHTML = '';
    const pre = document.createElement('pre');
    pre.textContent = rawMd;
    pre.style.cssText = 'font-family:var(--font-mono);font-size:12px;white-space:pre-wrap;color:var(--text-dim)';
    mdPanel.appendChild(pre);
    return;
  }
  // Preprocess Obsidian callouts → HTML blocks
  const preprocessed = preprocessObsidianCallouts(rawMd);
  // Configure marked to allow HTML + GFM tables
  window.marked.setOptions({ gfm: true, breaks: false, headerIds: false, mangle: false });
  // rawMd comes from an agent-generated *_result.md file, so marked's output
  // is untrusted HTML — always run it through DOMPurify before any innerHTML
  // assignment (never insert marked's output directly).
  let html = DOMPurify.sanitize(window.marked.parse(preprocessed));
  // Pass 2: render marked inside callout bodies (they contain un-parsed MD)
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  tmp.querySelectorAll('.callout-body[data-md]').forEach(el => {
    el.innerHTML = DOMPurify.sanitize(window.marked.parse(el.textContent));
    el.removeAttribute('data-md');
  });
  mdPanel.innerHTML = DOMPurify.sanitize(tmp.innerHTML);
  // Render LaTeX via KaTeX auto-render
  if (window.renderMathInElement) {
    window.renderMathInElement(mdPanel, {
      delimiters: [
        {left: '$$', right: '$$', display: true},
        {left: '$',  right: '$',  display: false},
        {left: '\\[', right: '\\]', display: true},
        {left: '\\(', right: '\\)', display: false},
      ],
      throwOnError: false,
    });
  }
}

function renderLog(lines) {
  const frag = document.createDocumentFragment();
  for (const line of lines) {
    const span = document.createElement('span');
    span.className = 'log-line ' + classifyLogLine(line);
    span.textContent = line + '\n';
    frag.appendChild(span);
  }
  logPre.textContent = '';
  logPre.appendChild(frag);
  logPre.classList.remove('empty');
  logPre.scrollTop = logPre.scrollHeight;
}

function classifyLogLine(line) {
  const l = line.toLowerCase();
  if (l.includes('error') || l.includes('traceback') || l.startsWith('error:')) return 'err';
  if (l.includes('warning')) return 'warn';
  if (l.includes('action=done') || l.includes('verdict')) return 'ok';
  if (l.match(/^\s*\[\d\d:\d\d:\d\d\]/)) return 'info';
  return 'hint';
}

function clearResults(msg) {
  htmlEmpty.style.display = 'flex';
  htmlEmpty.textContent = msg || 'no result yet — run a task to render here';
  resultIframe.style.display = 'none';
  resultIframe.src = 'about:blank';
  jsonPanel.textContent = '—';
  jsonPanel.classList.add('empty');
  mdPanel.innerHTML = '—';
  mdPanel.classList.add('empty');
  mdRawPanel.textContent = '—';
  mdRawPanel.classList.add('empty');
  state.mdSource = '';
  logPre.textContent = '—';
  logPre.classList.add('empty');
  logBadge.textContent = '0';
  dlHtml.classList.add('disabled');
  dlJson.classList.add('disabled');
}

//---------------------------------------------------------------------------
// Status line
//---------------------------------------------------------------------------
function setStatus(kind, text) {
  statusDot.className = 'status-dot ' + (kind || '');
  statusText.textContent = text;
}

//---------------------------------------------------------------------------
// Tabs
//---------------------------------------------------------------------------
function activateTab(target) {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x.dataset.tab === target));
  document.querySelectorAll('.panel').forEach(p =>
    p.classList.toggle('active', p.dataset.panel === target));
}
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => activateTab(t.dataset.tab));
});

//---------------------------------------------------------------------------
// Wire buttons
//---------------------------------------------------------------------------
btnMock.addEventListener('click', () => runPipeline(false));
btnLive.addEventListener('click', () => runPipeline(true));
btnCancel.addEventListener('click', () => cancelRun());
btnFormalize.addEventListener('click', () => requestFormalize());
btnNew.addEventListener('click', () => {
  editor.value = JSON.stringify({
    task_type: '',
    source_text: '',
    language_spec: { kind: 'natural', alphabet: ['a','b'], description: '' }
  }, null, 2);
  editorTitle.textContent = 'editor · untitled.json';
  state.exampleName = null;
  document.querySelectorAll('.example').forEach(el => el.classList.remove('active'));
  updateGutter();
  editor.focus();
});

// Cmd+Enter = mock run
document.addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key === 'Enter') {
    e.preventDefault();
    if (!state.running) runPipeline(false);
  }
});

//---------------------------------------------------------------------------
// Bootstrap
//---------------------------------------------------------------------------
updateGutter();
syncRunButtons();
loadProjects();
refreshRuns();
setStatus(null, 'ready');
