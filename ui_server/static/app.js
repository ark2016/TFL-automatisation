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
  if (isFailedResult(r) || (r && r.status === 'completed' && r.verdict === 'failure')) return 'the pipeline reported a failure for this run, there is nothing to formalize'
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
      if (src[k] !== undefined && (src[k] !== null || k === 'verdict' || k === 'confidence')) run[k] = src[k];
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
      if (e.event === 'verdict' && e.payload && e.payload.verdict !== undefined) run.liveVerdict = e.payload.verdict;
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
  const verdict = r.verdict || (isActive(r.status) ? r.liveVerdict : null);
  rhVerdict.textContent = verdict
    ? String(verdict) + (num(r.confidence) !== null ? ` (${num(r.confidence).toFixed(2)})` : '')
    : '—';
  const u = r.usage || {};
  rhCost.textContent = fmtUsd(u.estimated_cost_usd ?? 0);
  // input_tokens is the UNCACHED part only; prompt-cache reads/writes are billed
  // separately (0.1x / 1.25x) and already included in the cost above.
  const cacheRead = num(u.cache_read_input_tokens) || 0, cacheWrite = num(u.cache_creation_input_tokens) || 0;
  const cacheText = (cacheRead || cacheWrite)
    ? ` · cache ${fmtTok(cacheRead)} read / ${fmtTok(cacheWrite)} written` : '';
  rhUsage.textContent = r.usage
    ? `${u.calls ?? 0} calls · ${fmtTok(u.input_tokens)} in / ${fmtTok(u.output_tokens)} out${cacheText}`
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
// The gear opens the settings of the workspace on screen (pipelines or Deep Agent).
btnSettings.addEventListener('click', () => (isDeepMode() ? deepOpenSettings() : openSettings()));
$('settings-close').addEventListener('click', closeSettings);
$('settings-cancel').addEventListener('click', closeSettings);
$('settings-save').addEventListener('click', saveSettings);
settingsBack.addEventListener('click', (e) => { if (e.target === settingsBack) closeSettings(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeSettings(); deepCloseSettings(); } });

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

// Cmd+Enter = mock run (of the pipeline, or of the Deep Agent on its tab; never a live run)
document.addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key === 'Enter') {
    e.preventDefault();
    if (isDeepMode()) { deepRunShortcut(); return; }
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

//===========================================================================
// Deep Agent tab: tfl_deep_agent through /api/deep/* (ui_server/deep_routes.py)
//
// Input (text, or a PDF / PNG / JPG / WebP read as base64 in the browser),
// run controls (template, main model, budget, review points, mock script or
// live + confirm_spend) with the estimate, the plan, the timeline of tool
// calls with trust chips, the subagent / workflow tree with model and cost,
// the budget bar (task and day), the HITL cards (approve / edit / reject) and
// the report with its template switcher and evidence table.
// Trust labels and the evidence come from code (the run's ledger); this tab
// only shows them.
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
const DEEP_POINT_LABEL = {
  formalization: 'HITL 1 · transcription + IR',
  report: 'HITL 2 · report',
  execute: 'sandbox execute',
};
// submit_report fields a reviewer may edit at HITL 2 (tools/report.py ReportInput)
const DEEP_REPORT_TEXT_FIELDS = ['verdict', 'method', 'summary', 'hypothesis', 'construction', 'conclusion'];
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
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function deepDepth(path) {
  const s = String(path || '');
  return s ? s.split('/').length - 1 : 0;
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
  if (!k) return `unsupported file ${name || ''} (${type || 'unknown type'}): use a PDF, PNG, JPG, WebP or a text file`;
  const lim = num(maxMb);
  const n = num(size);
  if (k.kind === 'file' && lim !== null && n !== null && n > lim * 1024 * 1024) {
    return `the file is ${(n / 1048576).toFixed(1)} MB; the limit is ${lim} MB (intake.max_file_mb)`;
  }
  return null;
}
function deepB64FromDataUrl(s) {
  const t = String(s || '');
  const i = t.indexOf(',');
  return t.startsWith('data:') && i >= 0 ? t.slice(i + 1) : t;
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
function deepCreateBody(form) {
  const body = {};
  if (form.file) {
    if (!form.file.b64) return {error: 'the file is still being read'};
    body.file_b64 = form.file.b64;
    body.mime = form.file.mime;
    if (form.file.name) body.name = form.file.name;
  } else {
    const text = String(form.text || '').trim();
    if (!text) return {error: 'type the task text or drop a file'};
    body.text = text;
  }
  if (form.template) body.template = form.template;
  if (form.override) body.settings_override = form.override;
  if (form.autoApprove && form.mode !== 'live') body.auto_approve = true;
  if (form.mode === 'live') {
    if (form.autoApprove) return {error: 'auto-approve is for mock runs: a paid live run keeps its human review points'};
    const h = (form.override && form.override.hitl) || {};
    const off = Object.keys(h).filter(k => h[k] === false);
    if (off.length) return {error: `a paid live run keeps its review points (${off.join(', ')}): turn them off in the settings, not for one run`};
    if (form.confirm !== true) return {error: 'a live run makes paid API calls: tick "I confirm API spend" first'};
    body.live = true;
    body.confirm_spend = true;
  } else if (form.mode === 'inline') {
    let script;
    try { script = JSON.parse(String(form.mockInline || '')); }
    catch (e) { return {error: 'the inline replay script is not valid JSON: ' + e.message}; }
    if (!script || typeof script !== 'object' || Array.isArray(script) || !script.models
        || typeof script.models !== 'object' || !Object.keys(script.models).length) {
      return {error: 'the inline replay script needs a non-empty "models" object {role: [replies]}'};
    }
    body.mock_script = script;
  } else {
    if (!form.mockName) return {error: 'pick a replay script: mock mode needs one'};
    body.mock_script = form.mockName;
  }
  return {body};
}

// HITL 1 edit: the changed fields of {transcription, ir, class}, or {error}.
function deepHitl1Edits(args, form) {
  const a = args || {};
  const out = {};
  if (form.transcription !== undefined) {
    const t = String(form.transcription).trim();
    const orig = String(a.transcription || a.task_text || '').trim();
    if (t !== orig) {
      if (!t) return {error: 'the transcription is empty'};
      out.transcription = t;
    }
  }
  if (form.irText !== undefined && String(form.irText).trim()) {
    let ir;
    try { ir = JSON.parse(String(form.irText)); }
    catch (e) { return {error: 'the IR is not valid JSON: ' + e.message}; }
    if (!ir || typeof ir !== 'object' || Array.isArray(ir)) return {error: 'the IR must be a JSON object'};
    if (!deepSame(ir, a.ir)) out.ir = ir;
  }
  if (form.cls && form.cls !== a.class) {
    if (!('ir' in out)) return {error: `changing the class to ${form.cls} needs an IR for that class: edit the IR as well`};
    out.class = form.cls;
  }
  return {payload: out, changed: Object.keys(out)};
}

// HITL 2 edit: the changed report fields, or {error}. form.stepsText is the steps as JSON text.
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
    if (out[k] !== undefined && !out[k].trim()) return {error: `the ${k} must not be empty`};
  }
  if (form.stepsText !== undefined) {
    let steps;
    try { steps = JSON.parse(String(form.stepsText)); }
    catch (e) { return {error: 'the steps are not valid JSON: ' + e.message}; }
    if (!Array.isArray(steps) || !steps.length) return {error: 'the steps must be a non-empty JSON array'};
    if (!deepSame(steps, a.steps || [])) out.steps = steps;
  }
  return {payload: out, changed: Object.keys(out)};
}

// Any other stop (sandbox execute ...): the changed keys of the args object.
function deepArgsEdits(args, text) {
  let obj;
  try { obj = JSON.parse(String(text || '')); }
  catch (e) { return {error: 'the arguments are not valid JSON: ' + e.message}; }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return {error: 'the arguments must be a JSON object'};
  const a = args || {};
  const out = {};
  for (const k of Object.keys(obj)) if (!deepSame(obj[k], a[k])) out[k] = obj[k];
  return {payload: out, changed: Object.keys(out)};
}

// The body of POST /api/deep/runs/<id>/resume, or {error}.
function deepResumeBody(decision, opts) {
  const o = opts || {};
  if (!['approve', 'edit', 'reject'].includes(decision)) return {error: 'unknown decision ' + decision};
  if (Array.isArray(o.allowed) && o.allowed.length && !o.allowed.includes(decision)) {
    return {error: `${decision} is not allowed here (allowed: ${o.allowed.join(', ')})`};
  }
  const body = {decision};
  const comment = String(o.comment || '').trim();
  if (comment) body.comment = comment;
  if (decision === 'edit') {
    if (!o.payload || typeof o.payload !== 'object' || !Object.keys(o.payload).length) {
      return {error: 'nothing is edited: change a field first, or approve'};
    }
    body.payload = o.payload;
  } else if (decision === 'reject' && !comment) {
    return {error: 'reject needs a comment: the agent continues with it'};
  }
  if (o.live) {
    if (o.confirm !== true) return {error: 'this is a live run: resuming makes paid API calls, tick "I confirm API spend"'};
    body.confirm_spend = true;
  }
  return {body};
}

function deepBrief(obj, keys) {
  const parts = [];
  for (const k of keys) {
    const v = obj ? obj[k] : undefined;
    if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) continue;
    let t = Array.isArray(v) ? v.map(x => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join('; ')
      : (typeof v === 'object' ? JSON.stringify(v) : String(v));
    if (/_usd$/.test(k) && num(v) !== null) t = fmtUsd(v);
    parts.push(`${k.replace(/_/g, ' ')} ${t.length > 160 ? t.slice(0, 160) + '…' : t}`);
  }
  return parts.join(' · ');
}

// progress.jsonl events -> timeline rows (tfl_deep_agent/progress.py event kinds).
function deepTimeline(events) {
  const rows = [];
  (events || []).forEach((ev, i) => {
    const k = String(ev.event || '');
    const r = {i, event: k, time: deepTimeOf(ev.ts), depth: 0, title: k.replace(/_/g, ' '), sub: '',
      model: '', trust: null, trustReason: '', cost: null, elapsed: null, cls: '', group: 'other',
      detail: '', args: '', ok: null};
    switch (k) {
      case 'tool_call': {
        r.group = 'tools';
        r.depth = deepDepth(ev.scope);
        r.title = ev.tool === 'task' && ev.subagent_type ? `task → ${ev.subagent_type}` : String(ev.tool || 'tool');
        r.sub = [ev.role, ev.id].filter(Boolean).join(' · ');
        r.model = deepModelName(ev.model);
        r.trust = ev.trust || 'not_verified';
        r.trustReason = String(ev.trust_reason || '');
        r.cost = num(ev.cost_usd);
        r.elapsed = num(ev.elapsed_s);
        r.ok = ev.ok !== false && ev.status !== 'error' && ev.status !== 'exception';
        if (!r.ok) {
          r.cls = 'err';
          r.detail = [ev.error_kind, ev.error].filter(Boolean).join(': ');
        }
        r.args = String(ev.args_preview || '');
        break;
      }
      case 'model_call':
        r.group = 'agents';
        r.cls = 'model' + (ev.outcome && ev.outcome !== 'ok' ? ' warn' : '');
        r.depth = deepDepth(ev.node);
        r.title = `model · ${ev.role || ev.node || '?'}`;
        r.sub = [ev.node, (num(ev.input_tokens) !== null ? `${fmtTok(ev.input_tokens)}↑ ${fmtTok(ev.output_tokens)}↓` : '')]
          .filter(Boolean).join(' · ');
        r.model = deepModelName(ev.model);
        r.cost = num(ev.usd);
        if (ev.outcome && ev.outcome !== 'ok') r.detail = 'outcome: ' + ev.outcome;
        break;
      case 'subagent_start':
        r.group = 'agents';
        r.depth = Math.max(0, deepDepth(ev.node) - 1);
        r.title = `▸ ${String(ev.node || '').split('/').pop() || 'subagent'} started`;
        r.sub = [ev.kind, ev.node].filter(Boolean).join(' · ');
        r.detail = num(ev.allocated_usd) !== null ? `allocated ${fmtUsd(ev.allocated_usd)}` : '';
        break;
      case 'subagent_end':
        r.group = 'agents';
        r.depth = Math.max(0, deepDepth(ev.node) - 1);
        r.title = `◂ ${String(ev.node || '').split('/').pop() || 'subagent'} closed`;
        r.sub = String(ev.node || '');
        r.detail = num(ev.returned_usd) !== null ? `returned ${fmtUsd(ev.returned_usd)}` : '';
        break;
      case 'subagent_refused':
      case 'budget_wrap_up':
      case 'budget_refused':
        r.group = 'agents';
        r.cls = 'warn';
        r.sub = String(ev.node || '');
        r.detail = deepBrief(ev, ['reason', 'tool', 'available_usd', 'worst_case_usd']);
        break;
      case 'todos': {
        const t = Array.isArray(ev.todos) ? ev.todos : [];
        const doneN = t.filter(x => x && x.status === 'completed').length;
        r.group = 'agents';
        r.title = 'plan updated';
        r.sub = `${doneN}/${t.length} done` + (ev.role ? ' · ' + ev.role : '');
        break;
      }
      case 'hitl_awaiting': {
        r.group = 'hitl';
        r.cls = 'hitl';
        r.title = `⏸ ${DEEP_POINT_LABEL[ev.point] || ev.point || 'review'}`;
        r.sub = 'waiting for a human decision';
        const d = ev.draft;
        if (d && typeof d === 'object') {
          r.trust = d.trust || null;
          r.detail = deepBrief(d, ['verdict', 'confidence_cap', 'unknown_ids']);
        }
        break;
      }
      case 'hitl_decision':
        r.group = 'hitl';
        r.cls = 'hitl';
        r.title = `decision · ${ev.decision || '?'}`;
        r.sub = [DEEP_POINT_LABEL[ev.point] || ev.point, ev.source].filter(Boolean).join(' · ');
        r.detail = [Array.isArray(ev.edited_fields) && ev.edited_fields.length ? 'edited: ' + ev.edited_fields.join(', ') : '',
          ev.comment ? 'comment: ' + ev.comment : ''].filter(Boolean).join('\n');
        break;
      case 'segment_start':
        r.cls = 'segment';
        r.title = `segment ${ev.segment ?? '?'} · start`;
        r.sub = [ev.mode, ev.template].filter(Boolean).join(' · ');
        if (ev.resumed_with && typeof ev.resumed_with === 'object') {
          r.detail = 'resumed with: ' + deepBrief(ev.resumed_with, ['point', 'decision', 'edited_fields', 'comment']);
        }
        break;
      case 'segment_end':
        r.cls = 'segment' + (ev.status === 'error' ? ' err' : '');
        r.title = `segment ${ev.segment ?? '?'} · ${ev.status || 'end'}`;
        r.detail = ev.error ? String(ev.error) : '';
        break;
      case 'done':
        r.group = 'hitl';
        r.cls = ev.status === 'error' ? 'err' : 'done';
        r.title = `done · ${ev.status || '?'}`;
        r.sub = [ev.verdict, ev.language_class].filter(Boolean).join(' · ');
        r.trust = ev.trust || null;
        r.detail = [num(ev.confidence_cap) !== null ? `confidence cap ${num(ev.confidence_cap).toFixed(2)}` : '',
          ev.error ? String(ev.error) : ''].filter(Boolean).join(' · ');
        break;
      default:
        if (/^(spawn|workflow)_/.test(k)) {
          r.group = 'agents';
          r.cls = /refused|skipped/.test(k) ? 'warn' : '';
          r.title = k.replace(/_/g, ' ');
          r.sub = [ev.name, ev.workflow, ev.step].filter(Boolean).join(' / ');
          r.model = deepModelName(ev.model);
          r.detail = deepBrief(ev, ['status', 'budget_usd', 'spent_usd', 'reason', 'problems', 'layers', 'failed_deps']);
        }
        break;
    }
    rows.push(r);
  });
  return rows;
}
function deepFilterRows(rows, filter) {
  if (!filter || filter === 'all') return rows;
  return rows.filter(r => r.group === filter);
}

// The subagent / workflow tree of fold_events -> flat rows with ascii branches.
function deepFlattenTree(roots) {
  const out = [];
  const walk = (n, depth, prefix, last) => {
    const kids = Array.isArray(n.children) ? n.children : [];
    out.push({
      node: n.node, name: n.name || n.node, kind: n.kind, status: n.status, depth,
      branch: depth === 0 ? '' : prefix + (last ? '└ ' : '├ '),
      spent: num(n.spent_usd), subtree: num(n.subtree_spent_usd), allocated: num(n.allocated_usd),
      returned: num(n.returned_usd), calls: n.calls || 0, models: n.models || {}, roles: n.roles || [],
      children: kids.length,
    });
    const next = depth === 0 ? '' : prefix + (last ? '  ' : '│ ');
    kids.forEach((c, i) => walk(c, depth + 1, next, i === kids.length - 1));
  };
  (roots || []).forEach((r, i) => walk(r, 0, '', i === (roots.length - 1)));
  return out;
}

// The budget bar: task (spent / limit, reserve) and today's spend (detail.budget).
function deepBudgetView(budget, mode) {
  const b = budget || {};
  const pct = (s, l) => (s !== null && l) ? Math.max(0, Math.min(100, (s / l) * 100)) : 0;
  const spent = num(b.spent_usd), limit = num(b.limit_usd), reserve = num(b.reserve_usd);
  const tp = pct(spent, limit);
  const task = {
    spent, limit, reserve, pct: tp,
    reservePct: limit && reserve ? Math.min(100, (reserve / limit) * 100) : 0,
    state: b.exhausted ? 'over' : ((b.wrap_up || tp >= 80) ? 'warn' : ''),
    text: limit !== null ? `${fmtUsd(spent ?? 0)} / ${fmtUsd(limit)}` : '—',
  };
  const d = b.day || {};
  const ds = num(d.spent_usd), dl = num(d.limit_usd);
  const dp = pct(ds, dl);
  const day = {
    spent: ds, limit: dl, pct: dp,
    state: d.can_start === false ? 'over' : (dp >= 80 ? 'warn' : ''),
    text: dl !== null ? `${fmtUsd(ds ?? 0)} / ${fmtUsd(dl)}` : '—',
  };
  const notes = [];
  if (b.exhausted) notes.push('budget exhausted');
  else if (b.wrap_up) notes.push('wrap-up: only the report reserve is left');
  if (reserve) notes.push(`reserve ${fmtUsd(reserve)}`);
  if (mode === 'mock') notes.push('mock: token estimate, nothing billed');
  if (d.can_start === false) notes.push('day limit reached: a new live run is refused');
  return {task, day, note: notes.join(' · '), warn: !!(b.exhausted || b.wrap_up || d.can_start === false)};
}

// The estimate line under the run controls (GET /api/deep/estimate).
function deepEstimateView(est, form) {
  if (!est) return {text: 'estimate n/a', cls: ''};
  const lines = [];
  const day = est.day || {};
  const cap = num(est.cap_usd);
  if (form.mode !== 'live') {
    lines.push('mock replay: $0, nothing is sent to the API');
    lines.push(`if run live: hard cap ${fmtUsd(cap)} per task, reserve ${fmtUsd(est.reserve_for_report_usd)}`);
    return {text: lines.join('\n'), cls: ''};
  }
  const r = est.run || {};
  const avg = num(r.estimated_cost_usd);
  lines.push(avg === null ? 'measured: no data yet (no finished live deep run)'
    : `measured: ~${fmtUsd(avg)} (${fmtUsd(r.low_usd)}–${fmtUsd(r.high_usd)}, ${r.n} run${r.n === 1 ? '' : 's'})`);
  lines.push(`hard cap ${fmtUsd(cap)} per task · reserve ${fmtUsd(est.reserve_for_report_usd)} · subagent ≤ ${fmtUsd(est.per_subagent_max_usd)}`);
  const main = form.mainModel || (est.roles && est.roles.main) || '';
  const row = (Array.isArray(est.models) ? est.models : []).find(m => m.model === main || m.tier === main);
  if (row) lines.push(`main ${deepShortModel(row.model)}: ≤ ${fmtUsd(row.call_worst_case_usd)} per call (worst case)`);
  lines.push(`today ${fmtUsd(day.spent_usd ?? 0)} of ${fmtUsd(day.limit_usd)}`);
  const blocked = day.can_start === false;
  if (blocked) lines.push('the rest of the day budget is below one task cap: the server refuses a live run');
  return {text: lines.join('\n'), cls: blocked ? 'blocked' : 'strong'};
}

// result.json -> evidence table rows (ids in `basis` carry the verdict).
function deepEvidenceRows(result) {
  const r = result || {};
  const basis = new Set(Array.isArray(r.basis) ? r.basis : []);
  const refuted = new Set((Array.isArray(r.refuted_evidence) ? r.refuted_evidence : [])
    .map(x => (typeof x === 'string' ? x : (x && x.id))).filter(Boolean));
  const cited = {};
  (Array.isArray(r.steps) ? r.steps : []).forEach((s, i) => (s && Array.isArray(s.evidence_ids) ? s.evidence_ids : [])
    .forEach(id => { (cited[id] = cited[id] || []).push(i + 1); }));
  const rows = (Array.isArray(r.evidence) ? r.evidence : []).map(e => ({
    id: e.id, tool: e.tool || '', ok: e.ok !== false, trust: e.trust || 'not_verified',
    reason: String(e.trust_reason || e.error || ''), role: e.role || '', ir: e.ir_fingerprint || '',
    bounds: e.bounds ?? null, basis: basis.has(e.id), refuted: refuted.has(e.id), missing: false,
    steps: cited[e.id] || [],
  }));
  const known = new Set(rows.map(x => x.id));
  for (const id of (Array.isArray(r.unknown_ids) ? r.unknown_ids : [])) {
    if (known.has(id)) continue;
    rows.push({id, tool: '', ok: false, trust: null, reason: 'cited by the report, not in the evidence ledger',
      role: '', ir: '', bounds: null, basis: false, refuted: false, missing: true, steps: cited[id] || []});
  }
  return rows;
}

// The template the report was rendered with: result.json `rendered.template` (a HITL 2 edit
// may change it), else the run's template that the detail view reports.
function deepReportTemplate(res, j) {
  const r = j && typeof j === 'object' ? j : {};
  return (r.rendered && r.rendered.template) || (r.report && r.report.template) || (res && res.template) || '';
}
// Every tool call of a pending HITL stop (`actions` of the detail view; an older
// response carries only the first one as action / args).
function deepPendingActions(p) {
  if (!p) return [];
  if (Array.isArray(p.actions) && p.actions.length) return p.actions.filter(a => a && typeof a === 'object');
  if (p.action || p.args) return [{name: p.action, args: p.args, description: p.description, allowed_decisions: p.allowed_decisions}];
  return [];
}
function deepRunSub(r) {
  const st = r.status === 'awaiting_human' && r.pending_point ? `awaiting ${r.pending_point}` : r.status;
  return [st, r.mode, r.template, r.verdict, fmtWhen(toMs(r.created))].filter(Boolean).join(' · ');
}
// ---- deep: pure helpers end ----

//---------------------------------------------------------------------------
// Deep Agent: state + DOM refs
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
  filter: 'all',
  dtab: 'review',
  reportView: 'html',
  reviewKey: null,       // the HITL card on screen (kept while the reviewer edits)
  reportKey: null,
  resultJson: null,
  pickedTemplate: null,  // report template picked for a HITL 2 edit
  viewTpl: null,         // {runId, template, urls}: a finished report re-rendered for viewing (POST .../render)
  tlExpanded: new Set(),
  tlCount: -1,
  estSeq: 0,
  estTimer: null,
  estimate: null,
  dispatching: false,
};

const modeSwitch = $('mode-switch');
const deepView = $('deep-view');
const deepText = $('deep-text');
const deepDrop = $('deep-drop');
const deepFileInput = $('deep-file-input');
const deepTemplateSel = $('deep-template');
const deepMainModelSel = $('deep-main-model');
const deepBudgetInp = $('deep-budget');
const deepHitl1 = $('deep-hitl1');
const deepHitl2 = $('deep-hitl2');
const deepModeSel = $('deep-mode');
const deepMockInline = $('deep-mock-inline');
const deepAutoApprove = $('deep-auto-approve');
const deepEstimateEl = $('deep-estimate');
const deepConfirm = $('deep-confirm');
const deepRunBtn = $('deep-run');
const deepMsgEl = $('deep-msg');
const deepRunList = $('deep-run-list');
const deepTimelineEl = $('deep-timeline');
const deepPlanEl = $('deep-plan');
const deepReviewEl = $('deep-review');
const deepAgentsEl = $('deep-agents');
const deepReportEl = $('deep-report');
const deepLogEl = $('deep-log');
const deepCancelBtn = $('deep-cancel');
const deepSettingsBack = $('deep-settings-back');
const deepSettingsBody = $('deep-settings-body');
const deepSettingsStatus = $('deep-settings-status');

function isDeepMode() { return document.body.classList.contains('mode-deep'); }

function deepMsg(text, kind) {
  deepMsgEl.textContent = text || '';
  deepMsgEl.className = 'deep-msg' + (kind ? ' ' + kind : '');
}
function trustChip(t, title) {
  const c = el('span', deepTrustClass(t), deepTrustLabel(t));
  if (title) c.title = title;
  return c;
}
function deepFileUrl(rel) {
  return `${API}/deep/runs/${encodeURIComponent(deep.runId)}/files/${rel}`;
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
}

//---------------------------------------------------------------------------
// Run controls
//---------------------------------------------------------------------------
async function deepLoadSettings() {
  try {
    const d = await api('/deep/settings');
    deep.settings = d.settings || null;
    if (Array.isArray(d.templates) && d.templates.length) deep.templates = d.templates;
    if (d.error) deepMsg('the deep-agent settings file is invalid: ' + d.error + '\n(live runs are refused until it is fixed)', 'err');
  } catch (e) {
    deepMsg(e.status === 503 ? e.message : 'could not load the deep-agent settings — ' + e.message, 'err');
  }
  deepFillControls();
}

function deepFillControls() {
  const s = deep.settings || {};
  const tplNow = deepTemplateSel.value;
  deepTemplateSel.textContent = '';
  deepTemplateSel.appendChild(new Option('agent picks', ''));
  deep.templates.forEach(t => deepTemplateSel.appendChild(new Option(t, t)));
  deepTemplateSel.value = deep.templates.includes(tplNow) ? tplNow : '';
  const models = s.models || {};
  const roles = models.roles || {};
  const opts = Array.from(new Set([...(Array.isArray(models.allowed) ? models.allowed : []), roles.main].filter(Boolean)));
  deepMainModelSel.textContent = '';
  opts.forEach(m => {
    const o = new Option(deepShortModel(m) + (m === roles.main ? ' (settings)' : ''), m);
    o.title = m;
    deepMainModelSel.appendChild(o);
  });
  if (roles.main) deepMainModelSel.value = roles.main;
  const b = s.budget || {};
  deepBudgetInp.value = b.per_task_usd ?? '';
  deepBudgetInp.title = `per_task_usd (saved: ${fmtUsd(b.per_task_usd)}); the day limit is ${fmtUsd(b.per_day_usd)}`;
  const h = s.hitl || {};
  deepHitl1.checked = h.formalization !== false;
  deepHitl2.checked = h.report !== false;
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
  deepModeSel.appendChild(new Option('mock · inline replay script (JSON)', 'inline'));
  deepModeSel.appendChild(new Option('live · paid API calls', 'live'));
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
  return {
    text: deepText.value,
    file: deep.file,
    template: deepTemplateSel.value || null,
    mode: m.mode,
    mockName: m.mockName,
    mockInline: deepMockInline.value,
    confirm: deepConfirm.checked,
    autoApprove: deepAutoApprove.checked,
    mainModel: deepMainModelSel.value || null,
    override: deepOverride({
      perTaskUsd: deepBudgetInp.value,
      mainModel: deepMainModelSel.value || null,
      hitlFormalization: deepHitl1.checked,
      hitlReport: deepHitl2.checked,
    }, deep.settings),
  };
}

function deepSyncControls() {
  const m = deepModeValue();
  const live = m.mode === 'live';
  deepMockInline.classList.toggle('show', m.mode === 'inline');
  // auto-approve is for mock runs (eval, tests); the server refuses it with live
  if (live && deepAutoApprove.checked) deepAutoApprove.checked = false;
  deepAutoApprove.disabled = live;
  deepAutoApprove.parentElement.title = live ? 'not for live runs: a paid run keeps its human review points' : '';
  const row = deepRunBtn.parentElement;
  row.classList.toggle('live', live);
  $('deep-live-group').classList.toggle('armed', live && deepConfirm.checked);
  deepRunBtn.textContent = live ? 'run live · confirm_spend' : 'run mock';
  deepRunBtn.classList.toggle('btn-danger', live);
  deepRunBtn.classList.toggle('btn-primary', !live);
  deepRunBtn.disabled = deep.dispatching || (live && !deepConfirm.checked) || (!!deep.file && !deep.file.b64);
  const sel = deep.mocks.find(x => 'mock:' + x.name === deepModeSel.value);
  deepModeSel.title = sel ? [sel.description, sel.task ? 'task: ' + sel.task : ''].filter(Boolean).join('\n') : '';
  $('deep-input-meta').textContent = deep.file ? `${deep.file.label} file` : 'text';
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
    const v = deepEstimateView(est, form);
    deepEstimateEl.textContent = v.text;
    deepEstimateEl.className = 'deep-estimate' + (v.cls ? ' ' + v.cls : '');
    deepEstimateEl.title = (est.run && est.run.note) || '';
  } catch (e) {
    if (seq !== deep.estSeq) return;
    deepEstimateEl.textContent = 'estimate n/a — ' + e.message;
    deepEstimateEl.className = 'deep-estimate blocked';
  }
}

[deepTemplateSel, deepMainModelSel, deepBudgetInp, deepHitl1, deepHitl2].forEach(n =>
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
      + (f.b64 ? '' : ' · reading…');
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
  const kind = deepFileKind(file.name, file.type);
  const maxMb = deep.settings && deep.settings.intake ? deep.settings.intake.max_file_mb : null;
  const problem = deepFileProblem(file.name, file.type, file.size, maxMb);
  if (problem) { deepMsg(problem, 'err'); return; }
  if (kind.kind === 'text') {
    file.text().then(t => {
      deepClearFile();
      deepText.value = t;
      deepMsg(`loaded ${file.name} as text`);
    }).catch(e => deepMsg('could not read the file — ' + e.message, 'err'));
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
    deepMsg('could not read the file in the browser', 'err');
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

//---------------------------------------------------------------------------
// Start / cancel
//---------------------------------------------------------------------------
async function deepStartRun() {
  if (deep.dispatching) return;
  const form = deepFormValues();
  const res = deepCreateBody(form);
  if (res.error) { deepMsg(res.error, 'err'); return; }
  deep.dispatching = true;
  deepSyncControls();
  deepMsg(form.mode === 'live' ? 'starting a live run…' : 'starting a mock run…');
  try {
    const d = await sendJson('/deep/runs', res.body);
    const warn = Array.isArray(d.warnings) && d.warnings.length ? '\n' + d.warnings.join('\n') : '';
    deepMsg(`started ${d.run_id} (${d.mode})${warn}`, 'ok');
    if (form.mode === 'live') deepConfirm.checked = false;   // every paid run is confirmed again
    deepOpenRun(d.run_id);
    deepRefreshRuns();
  } catch (e) {
    deepMsg(e.message, 'err');
  } finally {
    deep.dispatching = false;
    deepSyncControls();
  }
}
function deepRunShortcut() {
  if (deepModeValue().mode !== 'live') deepStartRun();
}
deepRunBtn.addEventListener('click', deepStartRun);

async function deepCancelRun() {
  const r = deep.detail && deep.detail.run;
  if (!r) return;
  if (r.status === 'awaiting_human'
      && !window.confirm('Cancel this run? A run waiting for a review can no longer be resumed.')) return;
  deepCancelBtn.disabled = true;
  try {
    await sendJson(`/deep/runs/${encodeURIComponent(r.run_id)}/cancel`, {});
    deepStartPolling();
    deepRefreshRuns();
  } catch (e) {
    deepMsg('cancel failed — ' + e.message, 'err');
  } finally {
    deepCancelBtn.disabled = false;
  }
}
deepCancelBtn.addEventListener('click', deepCancelRun);

//---------------------------------------------------------------------------
// Deep runs list
//---------------------------------------------------------------------------
async function deepRefreshRuns(first) {
  let d;
  try { d = await api('/deep/runs'); } catch (e) { return; }
  deep.runs = Array.isArray(d.runs) ? d.runs : [];
  deepRenderRuns();
  if (first && !deep.runId) {
    const act = deep.runs.find(x => deepIsActive(x.status) || x.status === 'awaiting_human');
    if (act) deepOpenRun(act.run_id);
  }
}
function deepRenderRuns() {
  $('deep-run-count').textContent = deep.runs.length;
  deepRunList.textContent = '';
  if (!deep.runs.length) {
    deepRunList.appendChild(el('div', 'example-empty', 'no deep runs yet'));
    return;
  }
  deep.runs.forEach(r => {
    const item = el('div', 'run-item' + (r.run_id === deep.runId ? ' active' : ''));
    item.dataset.id = r.run_id;
    item.appendChild(el('span', 'ri-dot ' + (r.status || '')));
    const inp = r.input || {};
    const what = inp.kind && inp.kind !== 'text' ? `${String(inp.kind).toUpperCase()} ${inp.name || ''}`.trim() : 'text';
    item.appendChild(el('span', 'ri-title', `${what} · ${r.run_id}`));
    item.appendChild(el('span', 'ri-cost', num(r.spent_usd) === null ? '' : fmtUsd(r.spent_usd)));
    const sub = deepRunSub(r);
    item.appendChild(el('span', 'ri-sub', sub));
    item.title = sub + (r.error ? '\n' + r.error : '');
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
  Object.assign(deep, {runId: id, detail: null, events: [], cursor: 0, reviewKey: null, reportKey: null,
    resultJson: null, pickedTemplate: null, tlCount: -1});
  deep.tlExpanded = new Set();
  deepRunList.querySelectorAll('.run-item').forEach(n => n.classList.toggle('active', n.dataset.id === id));
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
      if (e.status === 404) { again = false; deepMsg(`run ${deep.runId} is gone — ${e.message}`, 'err'); }
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
  const prev = deep.detail && deep.detail.run ? deep.detail.run.status : null;
  deep.detail = d;
  const st = d.run ? d.run.status : null;
  if (st !== prev) {
    if (st === 'awaiting_human') deepActivateTab('review');
    else if (!deepIsActive(st) && d.result) deepActivateTab('report');
    if (prev !== null) deepRefreshRuns();
  }
  deepRenderAll();
  return deepIsActive(st);
}

function deepRenderAll() {
  deepRenderHead();
  deepRenderPlan();
  deepRenderTimeline();
  deepRenderReview();
  deepRenderAgents();
  deepRenderReport();
  deepRenderLog();
  deepRenderBudget();
}

//---------------------------------------------------------------------------
// Header, plan, timeline, budget bar, log
//---------------------------------------------------------------------------
function deepRenderHead() {
  const d = deep.detail;
  const r = d && d.run;
  const st = r ? r.status : '';
  $('dh-status').textContent = r ? (st === 'awaiting_human' ? 'awaiting review' : st) : '—';
  $('dh-status').className = 'pill ' + (st || '');
  $('dh-id').textContent = r ? r.run_id : (deep.runId || '—');
  const mode = $('dh-mode');
  mode.textContent = r ? (r.mode || (r.live ? 'live' : 'mock')) : '—';
  mode.className = 'dh-mode' + (r && r.live ? ' live' : '');
  const res = d && d.result;
  const tpl = res ? deepReportTemplate(res, deep.resultJson) : (r && r.template);
  $('dh-template').textContent = r ? (tpl || 'template: agent picks') : '—';
  const done = d && d.done;
  const verdict = (res && res.verdict) || (r && r.verdict) || (done && done.verdict) || null;
  $('dh-verdict').textContent = verdict || '—';
  const trust = (res && res.trust) || (r && r.trust) || (done && done.trust) || null;
  const cap = num((res && res.confidence_cap) ?? (r && r.confidence_cap));
  const tEl = $('dh-trust');
  tEl.textContent = '';
  if (trust) {
    tEl.appendChild(trustChip(trust, 'trust set by code from the evidence ledger (docs/VERDICT_POLICY.md)'));
    if (cap !== null) tEl.appendChild(el('span', 'dc-meta', ` cap ${cap.toFixed(2)}`));
  }
  const spent = firstNum(d && d.budget && d.budget.spent_usd, r && r.spent_usd, d && d.usage && d.usage.estimated_cost_usd);
  $('dh-cost').textContent = fmtUsd(spent ?? 0);
  $('dh-elapsed').textContent = r && num(r.elapsed) !== null ? fmtDur(r.elapsed) : '';
  const cancellable = r && (deepIsActive(st) || st === 'awaiting_human');
  deepCancelBtn.style.display = cancellable ? '' : 'none';
}

function deepRenderPlan() {
  const todos = (deep.detail && Array.isArray(deep.detail.todos)) ? deep.detail.todos : [];
  deepPlanEl.textContent = '';
  const doneN = todos.filter(t => t && t.status === 'completed').length;
  $('deep-plan-meta').textContent = todos.length ? `${doneN}/${todos.length} done` : '';
  if (!todos.length) {
    deepPlanEl.appendChild(el('div', 'progress-empty', deep.runId ? 'the agent has not written a plan (write_todos) yet' : 'no run open'));
    return;
  }
  todos.forEach(t => {
    const st = String((t && t.status) || 'pending');
    const row = el('div', 'todo ' + st);
    row.appendChild(el('span', 'tmark', st === 'completed' ? '☑' : (st === 'in_progress' ? '◐' : '☐')));
    row.appendChild(el('span', 'tcontent', String((t && t.content) || '')));
    deepPlanEl.appendChild(row);
  });
}

const DEEP_TL_MAX = 600;
function deepRenderTimeline(force) {
  const key = deep.events.length + ':' + deep.filter;
  if (!force && key === deep.tlCount) return;
  deep.tlCount = key;
  const atBottom = deepTimelineEl.scrollTop + deepTimelineEl.clientHeight >= deepTimelineEl.scrollHeight - 30;
  deepTimelineEl.textContent = '';
  if (!deep.runId) {
    deepTimelineEl.appendChild(el('div', 'progress-empty', 'open a deep run, or start one on the left'));
    return;
  }
  const rows = deepFilterRows(deepTimeline(deep.events), deep.filter);
  if (!rows.length) {
    const active = deep.detail && deep.detail.run && deepIsActive(deep.detail.run.status);
    deepTimelineEl.appendChild(el('div', 'progress-empty', active ? 'waiting for the first event…' : 'no events'));
    return;
  }
  const shown = rows.length > DEEP_TL_MAX ? rows.slice(-DEEP_TL_MAX) : rows;
  if (shown.length < rows.length) {
    deepTimelineEl.appendChild(el('div', 'tl-more', `${rows.length - shown.length} earlier events hidden (progress.jsonl has them all)`));
  }
  const frag = document.createDocumentFragment();
  shown.forEach(r => {
    const row = el('div', 'tl-row ' + r.event + (r.cls ? ' ' + r.cls : ''));
    row.appendChild(el('span', 'tl-time', r.time));
    const main = el('span', 'tl-main');
    main.style.paddingLeft = (r.depth * 14) + 'px';
    const title = el('span', 'tl-title', (r.ok === false ? '✗ ' : '') + r.title);
    main.appendChild(title);
    if (r.sub) main.appendChild(el('span', 'tl-sub', r.sub));
    if (r.model) {
      const m = el('span', 'tl-model', deepShortModel(r.model));
      m.title = r.model;
      main.appendChild(m);
    }
    row.appendChild(main);
    const right = el('span', 'tl-right');
    if (r.elapsed !== null && r.elapsed > 0) right.appendChild(el('span', null, fmtDur(r.elapsed)));
    if (r.cost !== null && r.cost > 0) right.appendChild(el('span', null, fmtUsd(r.cost)));
    if (r.trust) right.appendChild(trustChip(r.trust, r.trustReason));
    row.appendChild(right);
    const more = [r.detail, deep.tlExpanded.has(r.i) && r.trustReason ? 'trust: ' + r.trustReason : '',
      deep.tlExpanded.has(r.i) && r.args ? 'args: ' + r.args : ''].filter(Boolean).join('\n');
    if (more) row.appendChild(el('span', 'tl-detail', more));
    if (r.args || r.trustReason) {
      row.classList.add('clickable');
      row.title = 'click: arguments and the trust reason';
      row.addEventListener('click', () => {
        if (deep.tlExpanded.has(r.i)) deep.tlExpanded.delete(r.i); else deep.tlExpanded.add(r.i);
        deepRenderTimeline(true);
      });
    }
    frag.appendChild(row);
  });
  deepTimelineEl.appendChild(frag);
  if (atBottom || force === undefined) deepTimelineEl.scrollTop = deepTimelineEl.scrollHeight;
}
$('deep-filters').querySelectorAll('.dfilter').forEach(b => b.addEventListener('click', () => {
  deep.filter = b.dataset.filter;
  $('deep-filters').querySelectorAll('.dfilter').forEach(x => x.classList.toggle('active', x === b));
  deepRenderTimeline(true);
}));

function deepRenderBudget() {
  const d = deep.detail;
  const v = deepBudgetView(d ? d.budget : (deep.estimate ? {day: deep.estimate.day} : null),
    d && d.run ? d.run.mode : null);
  const fill = (id, part) => {
    const n = $(id);
    n.style.width = part.pct.toFixed(1) + '%';
    n.className = 'bbar-fill' + (part.state ? ' ' + part.state : '');
  };
  fill('deep-bb-task-fill', v.task);
  $('deep-bb-task-reserve').style.width = v.task.reservePct.toFixed(1) + '%';
  $('deep-bb-task-text').textContent = v.task.text;
  fill('deep-bb-day-fill', v.day);
  $('deep-bb-day-text').textContent = v.day.text;
  const note = $('deep-bb-note');
  note.textContent = v.note;
  note.className = 'bbar-note' + (v.warn ? ' warn' : '');
}

function deepRenderLog() {
  const d = deep.detail;
  const lines = d && Array.isArray(d.log_tail) ? d.log_tail : [];
  deepLogEl.textContent = lines.length
    ? lines.join('\n') + `\n\n(last ${lines.length} lines; the whole log: ${deepFileUrl('run.log')})`
    : (deep.runId ? 'no output yet' : '—');
  deepLogEl.classList.toggle('empty', !lines.length);
}

//---------------------------------------------------------------------------
// Right pane tabs
//---------------------------------------------------------------------------
function deepActivateTab(name) {
  deep.dtab = name;
  document.querySelectorAll('.dtab').forEach(t => t.classList.toggle('active', t.dataset.dtab === name));
  document.querySelectorAll('.dpanel').forEach(p => p.classList.toggle('active', p.dataset.dpanel === name));
}
document.querySelectorAll('.dtab').forEach(t => t.addEventListener('click', () => deepActivateTab(t.dataset.dtab)));
$('deep-widen').addEventListener('click', () => deepView.classList.toggle('wide'));

//---------------------------------------------------------------------------
// Agents tab: subagent / workflow tree, tool calls by trust, orchestration
//---------------------------------------------------------------------------
function deepCard(title, meta, cls) {
  const card = el('div', 'dcard' + (cls ? ' ' + cls : ''));
  const head = el('div', 'dcard-head');
  head.appendChild(el('span', null, title));
  if (meta) head.appendChild(meta instanceof Node ? meta : el('span', 'dc-meta', meta));
  card.appendChild(head);
  const body = el('div', 'dcard-body');
  card.appendChild(body);
  return {card, head, body};
}

function deepRenderAgents() {
  const d = deep.detail;
  deepAgentsEl.textContent = '';
  const rows = deepFlattenTree(d ? d.subagents : []);
  $('deep-agents-badge').textContent = Math.max(0, rows.length - 1);
  if (!d) {
    deepAgentsEl.appendChild(el('div', 'progress-empty', deep.runId ? 'loading…' : 'no run open'));
    return;
  }
  const tree = deepCard('subagents and workflow branches', rows.length ? `${fmtUsd(rows[0].subtree ?? 0)} total` : '');
  const t = el('div', 'dtree');
  if (!rows.length) t.appendChild(el('div', 'dnote', 'no model call yet'));
  rows.forEach(n => {
    const row = el('div', 'dnode ' + (n.status || ''));
    const name = el('span', 'dn-name');
    if (n.branch) name.appendChild(el('span', 'dn-branch', n.branch));
    name.appendChild(document.createTextNode(n.name));
    name.appendChild(el('span', 'dn-kind', [n.kind, n.status === 'closed' ? 'closed' : ''].filter(Boolean).join(' · ')));
    name.title = n.node;
    row.appendChild(name);
    const cost = el('span', 'dn-cost', fmtUsd(n.children ? n.subtree : n.spent));
    cost.title = n.children ? `own ${fmtUsd(n.spent)}, with children ${fmtUsd(n.subtree)}` : 'spent';
    row.appendChild(cost);
    const models = Object.entries(n.models).map(([m, usd]) => `${deepShortModel(m)} ${fmtUsd(usd)}`).join(', ');
    const sub = [models, n.calls ? `${n.calls} call${n.calls === 1 ? '' : 's'}` : '',
      n.roles.length ? 'roles ' + n.roles.join(', ') : '',
      n.allocated !== null ? `allocated ${fmtUsd(n.allocated)}` : '',
      n.returned !== null ? `returned ${fmtUsd(n.returned)}` : ''].filter(Boolean).join(' · ');
    const subEl = el('span', 'dn-sub', sub);
    subEl.style.paddingLeft = (n.depth * 14) + 'px';
    row.appendChild(subEl);
    t.appendChild(row);
  });
  tree.body.appendChild(t);
  deepAgentsEl.appendChild(tree.card);

  const tools = d.tools || {};
  const tc = deepCard('tool calls by trust', `${tools.total || 0} calls`);
  const sum = el('div', 'dtrust-sum');
  DEEP_TRUST_LEVELS.forEach(level => {
    const n = (tools.by_trust || {})[level];
    if (!n) return;
    sum.appendChild(trustChip(level));
    sum.appendChild(el('span', null, String(n)));
  });
  if (tools.failed) sum.appendChild(el('span', 'fail-mark', `· ${tools.failed} failed`));
  if (!tools.total) sum.appendChild(el('span', null, 'no tool call yet'));
  tc.body.appendChild(sum);
  tc.body.appendChild(el('div', 'dnote', 'trust is set by code from each tool result (docs/VERDICT_POLICY.md), never by the model'));
  deepAgentsEl.appendChild(tc.card);

  const orch = Array.isArray(d.orchestration) ? d.orchestration : [];
  const sig = Array.isArray(d.budget_signals) ? d.budget_signals : [];
  if (orch.length || sig.length) {
    const oc = deepCard('orchestration and budget signals', `${orch.length + sig.length}`);
    const list = el('div', 'dhist');
    deepTimeline(orch.concat(sig)).forEach(r => {
      const line = el('div', r.cls && r.cls.includes('warn') ? 'dnote warn' : null);
      line.appendChild(el('span', 'hd', `${r.time} ${r.title}`));
      const tail = [r.sub, r.model ? deepShortModel(r.model) : '', r.detail].filter(Boolean).join(' · ');
      if (tail) line.appendChild(document.createTextNode(' — ' + tail));
      list.appendChild(line);
    });
    oc.body.appendChild(list);
    deepAgentsEl.appendChild(oc.card);
  }
}

//---------------------------------------------------------------------------
// Review tab: HITL cards (approve / edit / reject)
//---------------------------------------------------------------------------
function deepRenderReview() {
  const d = deep.detail;
  const run = d && d.run;
  const hitl = (d && d.hitl) || {};
  const p = run && run.status === 'awaiting_human' ? hitl.pending : null;
  const badge = $('deep-review-badge');
  badge.textContent = p ? '1' : String((hitl.decisions || []).length);
  badge.classList.toggle('attn', !!p);
  const key = p ? `${deep.runId}:${p.interrupt_id}` : `${deep.runId}:${run ? run.status : ''}:${(hitl.decisions || []).length}`;
  if (key === deep.reviewKey) return;   // keep the reviewer's edits while polling
  deep.reviewKey = key;
  deepReviewEl.textContent = '';
  if (!d) {
    deepReviewEl.appendChild(el('div', 'progress-empty', deep.runId ? 'loading…' : 'no run open'));
    return;
  }
  if (p) {
    if (!deepView.classList.contains('wide') && p.point !== 'execute') deepView.classList.add('wide');
    if (p.point === 'formalization') deepReviewEl.appendChild(deepHitl1Card(p, run));
    else if (p.point === 'report') deepReviewEl.appendChild(deepHitl2Card(p, run));
    else deepReviewEl.appendChild(deepGenericCard(p, run));
  } else {
    const msg = deepIsActive(run && run.status) ? 'the agent is working; a card appears here when it stops for a review'
      : (run && run.status === 'awaiting_human' ? 'waiting for a review, but the agent record has no pending stop'
        : 'no review pending');
    deepReviewEl.appendChild(el('div', 'progress-empty', msg));
  }
  deepReviewEl.appendChild(deepHistoryCard(hitl));
}

function deepHistoryCard(hitl) {
  const c = deepCard('review history', '');
  const pts = hitl.points || {};
  c.head.lastChild.textContent = Object.keys(pts).length
    ? 'points: ' + Object.entries(pts).map(([k, v]) => `${k} ${v ? 'on' : 'off'}`).join(', ')
      + (hitl.auto_approve ? ' · auto-approve' : '') : '';
  const list = el('div', 'dhist');
  const decisions = Array.isArray(hitl.decisions) ? hitl.decisions : [];
  if (!decisions.length) list.appendChild(el('div', 'dnote', 'no decision yet'));
  decisions.forEach(x => {
    const line = el('div');
    line.appendChild(el('span', 'hd', `${deepTimeOf(x.ts)} ${DEEP_POINT_LABEL[x.point] || x.point}: ${x.decision}`));
    const tail = [x.source && x.source !== 'human' ? x.source : '',
      Array.isArray(x.edited_fields) && x.edited_fields.length ? 'edited ' + x.edited_fields.join(', ') : '',
      x.comment ? '«' + x.comment + '»' : ''].filter(Boolean).join(' · ');
    if (tail) line.appendChild(document.createTextNode(' — ' + tail));
    list.appendChild(line);
  });
  c.body.appendChild(list);
  return c.card;
}

function deepField(label, input, hint) {
  const f = el('div', 'dfield');
  const lab = el('label', null, label);
  if (input.id) lab.htmlFor = input.id;
  f.appendChild(lab);
  f.appendChild(input);
  if (hint) f.appendChild(el('span', 'dnote', hint));
  return f;
}
function deepTextarea(value, cls, rows) {
  const t = document.createElement('textarea');
  t.value = value ?? '';
  t.spellcheck = false;
  if (cls) t.className = cls;
  if (rows) t.rows = rows;
  return t;
}
function deepSelect(options, value) {
  const s = document.createElement('select');
  options.forEach(o => {
    const [v, label] = Array.isArray(o) ? o : [o, o];
    s.appendChild(new Option(label, v));
  });
  s.value = value ?? '';
  return s;
}

function deepSourcePreview(p, run) {
  const box = el('div', 'dsrc');
  const src = p.source || {};
  const inp = (run && run.input) || {};
  const url = p.source_url || (inp.file ? deepFileUrl(inp.file) : null);
  const mime = String(src.mime_type || src.mime || inp.mime || '');
  const title = el('div', 'dsec-title', 'source');
  const what = [src.kind || inp.kind, src.name || inp.name, num(src.pages ?? inp.pages) !== null ? `${src.pages ?? inp.pages} p.` : ''].filter(Boolean).join(' · ');
  if (what) title.appendChild(el('span', 'hint', what));
  if (url) {
    const a = el('a', 'link hint', 'open ↗');
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener';
    title.appendChild(a);
  }
  box.appendChild(title);
  if (!url) {
    box.appendChild(el('div', 'dnote', 'the source file is not available'));
  } else if (mime === 'application/pdf') {
    const f = document.createElement('iframe');
    f.src = url;
    f.title = 'source PDF';
    box.appendChild(f);
  } else if (mime.startsWith('image/')) {
    const img = document.createElement('img');
    img.src = url;
    img.alt = 'source image';
    box.appendChild(img);
  } else {
    const pre = el('pre', null, 'loading…');
    fetch(url, {cache: 'no-store'}).then(r => (r.ok ? r.text() : Promise.reject(new Error(r.status))))
      .then(t => { pre.textContent = t; }).catch(() => { pre.textContent = 'could not load the source'; });
    box.appendChild(pre);
  }
  return box;
}

function deepWordsBlock(ex) {
  const box = el('div', 'dfield');
  const e = ex || {};
  const lbl = el('span', 'lbl', 'sample words');
  box.appendChild(lbl);
  if (!Array.isArray(e.in_language) && !Array.isArray(e.not_in_language)) {
    box.appendChild(el('span', 'dnote', 'no sample words (the IR has no oracle for them)'));
    return box;
  }
  const line = (label, words, cls) => {
    const w = el('div', 'words');
    w.appendChild(el('span', 'wlbl', label));
    (words || []).forEach(x => w.appendChild(el('span', 'word ' + cls, deepWord(x))));
    if (!(words || []).length) w.appendChild(el('span', 'dnote', '—'));
    return w;
  };
  box.appendChild(line('in L', e.in_language, 'in'));
  box.appendChild(line('not in L', e.not_in_language, 'out'));
  box.appendChild(el('span', 'dnote', `labels: ${e.labels_from || '?'}, length ≤ ${e.max_len ?? '?'}`));
  return box;
}

// The decision bar of a card: comment, approve / edit / reject, confirm_spend for a live run.
function deepDecideBar(p, run, getEdits, opts) {
  const o = opts || {};
  const bar = el('div', 'ddecide');
  const allowed = Array.isArray(p.allowed_decisions) && p.allowed_decisions.length ? p.allowed_decisions : ['approve', 'reject'];
  const comment = deepTextarea('', null, 2);
  comment.placeholder = 'comment for the agent (needed to reject; optional otherwise)';
  bar.appendChild(comment);
  const row = el('div', 'drow');
  const live = !!(run && run.live);
  let confirm = null;
  if (live) {
    const lab = el('label', 'check danger');
    confirm = document.createElement('input');
    confirm.type = 'checkbox';
    lab.appendChild(confirm);
    lab.appendChild(el('span', null, 'I confirm API spend'));
    const g = el('span', 'live-group');
    g.appendChild(lab);
    confirm.addEventListener('change', () => g.classList.toggle('armed', confirm.checked));
    row.appendChild(g);
  }
  const msg = el('span', 'dmsg');
  const btns = [];
  const send = async (decision) => {
    let payload;
    if (decision === 'edit') {
      const e = getEdits();
      if (e.error) { msg.textContent = e.error; msg.className = 'dmsg err'; return; }
      payload = e.payload;
    }
    const res = deepResumeBody(decision, {payload, comment: comment.value, live, confirm: confirm ? confirm.checked : false, allowed});
    if (res.error) { msg.textContent = res.error; msg.className = 'dmsg err'; return; }
    btns.forEach(b => { b.disabled = true; });
    msg.textContent = 'sending…';
    msg.className = 'dmsg';
    try {
      const r = await sendJson(`/deep/runs/${encodeURIComponent(deep.runId)}/resume`, res.body);
      msg.textContent = `${r.decision} sent: the run continues`;
      msg.className = 'dmsg ok';
      deep.pickedTemplate = null;
      deepStartPolling();
      deepRefreshRuns();
    } catch (e) {
      msg.textContent = e.message;
      msg.className = 'dmsg err';
      btns.forEach(b => { b.disabled = false; });
      if (o.onError) o.onError();
    }
  };
  const mk = (decision, label, cls) => {
    if (!allowed.includes(decision)) return null;
    const b = el('button', 'btn btn-sm' + (cls ? ' ' + cls : ''), label);
    b.addEventListener('click', () => send(decision));
    btns.push(b);
    row.appendChild(b);
    return b;
  };
  mk('approve', o.approveLabel || 'Approve', 'btn-primary');
  const editBtn = mk('edit', 'Save edits');
  mk('reject', 'Reject with comment', 'btn-danger');
  row.appendChild(msg);
  bar.appendChild(row);
  if (live) bar.appendChild(el('span', 'dnote warn', 'live run: the next segment makes paid API calls'));
  return {bar, editBtn, msg};
}

// mark changed fields + label of the edit button
function deepWatchEdits(inputs, getEdits, editBtn) {
  const update = () => {
    const e = getEdits();
    const changed = new Set(e.changed || []);
    inputs.forEach(([key, node]) => {
      const f = node.closest('.dfield');
      if (f) f.classList.toggle('changed', changed.has(key));
    });
    if (editBtn) {
      editBtn.textContent = e.error ? 'Save edits (invalid)' : (changed.size ? `Save edits (${[...changed].join(', ')})` : 'Save edits');
      editBtn.title = e.error || '';
    }
  };
  inputs.forEach(([, node]) => { node.addEventListener('input', update); node.addEventListener('change', update); });
  update();
  return update;
}

function deepHitl1Card(p, run) {
  const a = p.args || {};
  const meta = [a.class ? 'class ' + a.class : '', a.ir_fingerprint, deepTimeOf(p.asked_at)].filter(Boolean).join(' · ');
  const c = deepCard(DEEP_POINT_LABEL.formalization, meta, 'attn');
  c.body.appendChild(el('div', 'dnote',
    'Every oracle check after this point is relative to the IR you approve here. Approve it, edit the transcription / IR, or send it back with a comment.'));
  const split = el('div', 'dsplit');
  split.appendChild(deepSourcePreview(p, run));
  const fields = el('div', 'dfields');
  const tr = deepTextarea(a.transcription || a.task_text || '', null, 6);
  fields.appendChild(deepField('transcription (intake)', tr));
  const cls = deepSelect(DEEP_CLASSES, a.class || '');
  const valid = a.valid === true ? 'validate_ir: valid' : (a.valid === false ? 'validate_ir: INVALID' : 'not validated');
  const clsField = deepField('class', cls, valid);
  const row = el('div', 'dfield-row');
  row.appendChild(clsField);
  if (a.ir_source || a.ir_error) {
    row.appendChild(el('div', 'dnote' + (a.ir_error ? ' err' : ''), a.ir_error ? 'IR: ' + a.ir_error : a.ir_source));
  }
  fields.appendChild(row);
  const ir = deepTextarea(a.ir ? JSON.stringify(a.ir, null, 2) : '', 'code', 14);
  fields.appendChild(deepField('IR (JSON)', ir));
  const problems = [].concat(Array.isArray(a.errors) ? a.errors : [], Array.isArray(a.warnings) ? a.warnings.map(w => 'warning: ' + w) : []);
  if (problems.length) fields.appendChild(el('div', 'dnote warn', problems.join('\n')));
  if (a.restatement) {
    const f = el('div', 'dfield');
    f.appendChild(el('span', 'lbl', 'restatement of the IR (ir_to_text, code)'));
    f.appendChild(el('pre', 'dpre', a.restatement));
    fields.appendChild(f);
  }
  fields.appendChild(deepWordsBlock(a.examples));
  if (a.ir_builder_answer) {
    const dr = el('details', 'drawer');
    dr.appendChild(el('summary', null, 'ir-builder answer'));
    dr.appendChild(el('pre', 'dpre', a.ir_builder_answer));
    fields.appendChild(dr);
  }
  split.appendChild(fields);
  c.body.appendChild(split);
  const getEdits = () => deepHitl1Edits(a, {transcription: tr.value, irText: ir.value, cls: cls.value});
  const dec = deepDecideBar(p, run, getEdits);
  c.card.appendChild(dec.bar);
  deepWatchEdits([['transcription', tr], ['ir', ir], ['class', cls]], getEdits, dec.editBtn);
  return c.card;
}

function deepHitl2Card(p, run) {
  const a = p.args || {};
  const draft = p.draft || {};
  const meta = el('span', 'dc-meta');
  if (draft.trust) meta.appendChild(trustChip(draft.trust, 'trust of the draft, set by code'));
  if (num(draft.confidence_cap) !== null) meta.appendChild(document.createTextNode(` cap ${num(draft.confidence_cap).toFixed(2)} · `));
  meta.appendChild(document.createTextNode(deepTimeOf(p.asked_at)));
  const c = deepCard(DEEP_POINT_LABEL.report, meta, 'attn');
  if (Array.isArray(draft.unknown_ids) && draft.unknown_ids.length) {
    c.body.appendChild(el('div', 'dbanner err', 'the draft cites ids that are not in the evidence ledger: '
      + draft.unknown_ids.join(', ') + '\nthey lower the trust of the steps that cite them'));
  }
  const split = el('div', 'dsplit');
  // preview of the rendered draft
  const prev = el('div', 'dsrc');
  const pt = el('div', 'dsec-title', 'draft preview');
  const urls = p.draft_urls || {};
  ['html', 'md', 'json'].forEach(s => {
    if (!urls[s]) return;
    const a2 = el('a', 'link hint', s + ' ↗');
    a2.href = urls[s];
    a2.target = '_blank';
    a2.rel = 'noopener';
    pt.appendChild(a2);
  });
  prev.appendChild(pt);
  if (urls.html) {
    const f = document.createElement('iframe');
    f.className = 'dreport-frame';
    f.setAttribute('sandbox', 'allow-scripts');
    f.title = 'draft report';
    f.src = urls.html;
    prev.appendChild(f);
  } else {
    prev.appendChild(el('div', 'dnote', 'no rendered draft'));
  }
  split.appendChild(prev);
  // editable fields
  const fields = el('div', 'dfields');
  const tplRow = el('div', 'dfield');
  tplRow.appendChild(el('span', 'lbl', 'template'));
  const tpl = deepSelect(deep.templates, deep.pickedTemplate || a.template || 'full_proof');
  tpl.id = 'dh2-template';
  const sw = el('div', 'dtpl');
  const syncSw = () => sw.querySelectorAll('button').forEach(b => {
    b.classList.toggle('active', b.dataset.t === tpl.value);
  });
  deep.templates.forEach(t => {
    const b = el('button', null, t);
    b.dataset.t = t;
    b.title = t === (a.template || 'full_proof') ? 'the template of the draft' : 'render the report with this template (Save edits)';
    b.addEventListener('click', () => { tpl.value = t; tpl.dispatchEvent(new Event('change')); syncSw(); });
    sw.appendChild(b);
  });
  tpl.addEventListener('change', () => { deep.pickedTemplate = tpl.value; syncSw(); });
  syncSw();
  tpl.style.display = 'none';
  tplRow.appendChild(sw);
  tplRow.appendChild(tpl);
  fields.appendChild(tplRow);
  const inputs = [['template', tpl]];
  const text = (key, label, rows) => {
    const t = rows ? deepTextarea(a[key] || '', null, rows) : document.createElement('input');
    if (!rows) { t.type = 'text'; t.value = a[key] || ''; t.spellcheck = false; }
    inputs.push([key, t]);
    return deepField(label, t);
  };
  fields.appendChild(text('verdict', 'verdict'));
  const r2 = el('div', 'dfield-row');
  const ans = deepSelect([['', '—'], 'yes', 'no', 'inconclusive'], a.answer ?? '');
  inputs.push(['answer', ans]);
  r2.appendChild(deepField('answer', ans));
  const lc = deepSelect(DEEP_CLASSES, a.language_class || '');
  inputs.push(['language_class', lc]);
  r2.appendChild(deepField('language class', lc));
  fields.appendChild(r2);
  fields.appendChild(text('method', 'method'));
  fields.appendChild(text('summary', 'summary', 3));
  const more = el('details', 'drawer');
  more.appendChild(el('summary', null, 'hypothesis, construction, conclusion, steps'));
  const moreBody = el('div', 'dfields');
  moreBody.appendChild(text('hypothesis', 'hypothesis', 2));
  moreBody.appendChild(text('construction', 'construction', 3));
  moreBody.appendChild(text('conclusion', 'conclusion', 2));
  const steps = deepTextarea(JSON.stringify(a.steps || [], null, 2), 'code', 12);
  inputs.push(['steps', steps]);
  moreBody.appendChild(deepField('steps (JSON)', steps, 'claims, justifications and the evidence ids they cite; trust per step is recomputed by code'));
  more.appendChild(moreBody);
  fields.appendChild(more);
  fields.appendChild(el('div', 'dnote', 'the evidence block and the trust labels of the report are generated by code: an edit re-renders the report and re-checks the cited ids'));
  split.appendChild(fields);
  c.body.appendChild(split);
  const getEdits = () => {
    const form = {stepsText: steps.value};
    inputs.forEach(([k, n]) => { if (k !== 'steps') form[k] = n.value; });
    return deepReportEdits(a, form);
  };
  const dec = deepDecideBar(p, run, getEdits, {approveLabel: 'Approve report'});
  c.card.appendChild(dec.bar);
  deepWatchEdits(inputs, getEdits, dec.editBtn);
  return c.card;
}

function deepGenericCard(p, run) {
  const c = deepCard(DEEP_POINT_LABEL[p.point] || `review · ${p.point || p.action || '?'}`, deepTimeOf(p.asked_at), 'attn');
  const actions = deepPendingActions(p);
  // one stop may hold several tool calls; the decision applies to every one of them
  if (actions.length > 1) {
    c.body.appendChild(el('div', 'dbanner warn', `${actions.length} actions wait in this stop: Approve or Reject applies to all of them (edit needs a single action)`));
  }
  const editable = actions.length === 1 && (p.allowed_decisions || []).includes('edit');
  let argsBox = null;
  actions.forEach((a, i) => {
    if (a.description) c.body.appendChild(el('pre', 'dpre', a.description));
    const box = deepTextarea(JSON.stringify(a.args || {}, null, 2), 'code', actions.length > 1 ? 8 : 12);
    box.readOnly = !editable;
    const label = (actions.length > 1 ? `${i + 1}/${actions.length} · ` : '') + `${a.name || 'action'} arguments`
      + (editable ? ' (editable)' : '');
    c.body.appendChild(deepField(label, box));
    if (i === 0) argsBox = box;
  });
  if (p.card) {
    const det = el('details', 'drawer');
    det.appendChild(el('summary', null, 'review card (hitl.md)'));
    det.appendChild(el('pre', 'dpre', p.card));
    c.body.appendChild(det);
  }
  const first = actions[0] || {};
  const getEdits = () => (argsBox ? deepArgsEdits(first.args || {}, argsBox.value) : {error: 'nothing to edit'});
  const dec = deepDecideBar(p, run, getEdits);
  c.card.appendChild(dec.bar);
  if (editable) deepWatchEdits([], getEdits, dec.editBtn);
  return c.card;
}

//---------------------------------------------------------------------------
// Report tab: template switcher, rendered report, evidence table
//---------------------------------------------------------------------------
function deepRenderReport() {
  const d = deep.detail;
  const res = d && d.result;
  const run = d && d.run;
  const pending = d && d.hitl && run && run.status === 'awaiting_human' ? d.hitl.pending : null;
  const viewing = res && deep.viewTpl && deep.viewTpl.runId === deep.runId ? deep.viewTpl : null;
  const key = res ? `${deep.runId}:${res.status}:${res.template}:${Object.values(res.urls || {}).join(',')}:${deep.reportView}:${pending ? pending.interrupt_id : ''}:${viewing ? viewing.template : ''}`
    : `${deep.runId}:none:${run ? run.status : ''}`;
  if (key === deep.reportKey) return;
  deep.reportKey = key;
  deepReportEl.textContent = '';
  if (!res) {
    let msg = deep.runId ? 'no report for this run' : 'no run open';
    if (run && deepIsActive(run.status)) msg = 'the report appears when the agent submits it';
    else if (pending && pending.point === 'formalization') msg = 'no report yet: the transcription and the IR are waiting for review (Review tab)';
    deepReportEl.appendChild(el('div', 'progress-empty', msg));
    return;
  }
  const head = el('span', 'dc-meta');
  if (res.trust) head.appendChild(trustChip(res.trust, 'set by code from the evidence ledger'));
  if (num(res.confidence_cap) !== null) head.appendChild(document.createTextNode(` cap ${num(res.confidence_cap).toFixed(2)}`));
  const c = deepCard(`report · ${res.draft ? 'draft' : (res.status || '?')}`, head);
  const kv = el('div', 'dkv');
  const kvRow = (k, v) => { if (v === null || v === undefined || v === '') return; kv.appendChild(el('span', 'k', k)); kv.appendChild(el('span', 'v', String(v))); };
  kvRow('verdict', res.verdict || '—');
  kvRow('class', res.language_class);
  kvRow('review', res.human_review);
  kvRow('reason', res.reason);
  c.body.appendChild(kv);
  if (res.draft) {
    c.body.appendChild(el('div', 'dbanner', 'draft: the report waits for approval at HITL 2 (Review tab). A template picked below is applied with "Save edits" there.'));
  } else if (res.status && res.status !== 'submitted') {
    c.body.appendChild(el('div', 'dbanner err', `the run ended without an accepted report: ${res.status}` + (res.reason ? ` (${res.reason})` : '')));
  } else if (res.human_review) {
    c.body.appendChild(el('div', 'dbanner ok', res.human_review));
  }
  // template switcher
  const swRow = el('div', 'dsec-title', 'template');
  const sw = el('div', 'dtpl');
  const canPick = !!(res.draft && pending && pending.point === 'report' && (pending.allowed_decisions || []).includes('edit'));
  const canView = !canPick && !!res.can_render;   // a finished, accepted report: other templates as a view
  const current = deepReportTemplate(res, deep.resultJson);
  const shown = viewing ? viewing.template : current;
  deep.templates.forEach(t => {
    const b = el('button', null, t);
    b.classList.toggle('active', t === shown);
    b.classList.toggle('picked', canPick && deep.pickedTemplate === t && t !== current);
    if (canPick) {
      b.title = t === current ? 'the template of the draft' : 'render the report with this template: picked for the HITL 2 edit';
      b.addEventListener('click', () => {
        deep.pickedTemplate = t;
        deep.reviewKey = null;   // rebuild the HITL 2 card with the picked template
        deepRenderReview();
        deep.reportKey = null;
        deepRenderReport();
        deepActivateTab('review');
      });
    } else if (canView) {
      b.title = t === current ? 'the template the report was approved with'
        : 'view the report in this template (rendered by code from result.json; result.* keeps its template)';
      b.addEventListener('click', async () => {
        if (t === shown) return;
        if (t === current) { deep.viewTpl = null; deep.reportKey = null; deepRenderReport(); return; }
        const id = deep.runId;
        sw.querySelectorAll('button').forEach(x => { x.disabled = true; });
        try {
          const r = await sendJson(`/deep/runs/${encodeURIComponent(id)}/render`, {template: t});
          if (id !== deep.runId) return;
          deep.viewTpl = {runId: id, template: t, urls: r.urls || {}};
        } catch (e) {
          deepMsg(e.message, 'err');
        }
        deep.reportKey = null;
        deepRenderReport();
      });
    } else {
      b.disabled = t !== current;
      b.title = t === current ? 'the template this report was rendered with'
        : 'only an accepted report can be viewed in another template; pick it for the next run (the composer on the left) or at HITL 2';
    }
    sw.appendChild(b);
  });
  swRow.appendChild(sw);
  if (!canPick && current) {
    const use = el('button', 'btn btn-sm', 'use for the next run');
    use.title = 'set the template of the run controls on the left';
    use.addEventListener('click', () => { deepTemplateSel.value = shown; deepSyncControls(); deepRefreshEstimateSoon(); });
    swRow.appendChild(use);
  }
  c.body.appendChild(swRow);
  if (viewing) {
    c.body.appendChild(el('div', 'dbanner', `a view in ${viewing.template}: the report was rendered (and reviewed) as ${current}, and result.* keeps that template`));
  }
  // view toggle + links
  const vt = el('div', 'dsec-title');
  const tog = el('div', 'dsub-toggle');
  const urls = Object.assign({}, res.urls || {}, viewing ? viewing.urls : {});
  [['html', 'report'], ['md', 'markdown'], ['json', 'json']].forEach(([v, label]) => {
    if (!urls[v]) return;
    const b = el('button', deep.reportView === v ? 'active' : null, label);
    b.addEventListener('click', () => { deep.reportView = v; deep.reportKey = null; deepRenderReport(); });
    tog.appendChild(b);
  });
  vt.appendChild(tog);
  Object.entries(urls).forEach(([s, u]) => {
    const a = el('a', 'link hint', s + ' ↗');
    a.href = u;
    a.target = '_blank';
    a.rel = 'noopener';
    vt.appendChild(a);
  });
  c.body.appendChild(vt);
  const view = urls[deep.reportView] ? deep.reportView : (urls.html ? 'html' : (urls.md ? 'md' : 'json'));
  if (view === 'html' && urls.html) {
    const f = document.createElement('iframe');
    f.className = 'dreport-frame';
    f.setAttribute('sandbox', 'allow-scripts');   // the report's own tab JS runs; no same-origin access
    f.title = 'report';
    f.src = urls.html;
    c.body.appendChild(f);
  } else if (view === 'md' && urls.md) {
    const box = el('div', 'md-render dreport-md', 'loading…');
    fetch(urls.md, {cache: 'no-store'}).then(r => r.text()).then(t => deepRenderMarkdownInto(box, t)).catch(() => { box.textContent = 'could not load result.md'; });
    c.body.appendChild(box);
  } else if (urls.json) {
    const pre = el('pre', 'dpre', 'loading…');
    fetch(urls.json, {cache: 'no-store'}).then(r => r.text()).then(t => {
      try { pre.textContent = JSON.stringify(JSON.parse(t), null, 2); } catch (e) { pre.textContent = t; }
    }).catch(() => { pre.textContent = 'could not load result.json'; });
    c.body.appendChild(pre);
  }
  deepReportEl.appendChild(c.card);
  // evidence table (result.json: generated by code from the evidence ledger)
  const ev = deepCard('evidence', 'from the run\'s evidence ledger; trust set by code');
  ev.body.appendChild(el('div', 'dnote', 'loading result.json…'));
  deepReportEl.appendChild(ev.card);
  if (urls.json) {
    const id = deep.runId;
    fetch(urls.json, {cache: 'no-store'}).then(r => r.json()).then(j => {
      if (id !== deep.runId) return;
      deep.resultJson = j;
      if (deepReportTemplate(res, j) !== current) {   // rendered with another template: redraw the switcher
        deep.reportKey = null;
        deepRenderReport();
        deepRenderHead();
        return;
      }
      ev.body.textContent = '';
      deepEvidenceInto(ev.body, j);
    }).catch(() => { ev.body.textContent = 'could not load result.json'; });
  } else {
    ev.body.textContent = 'no result.json';
  }
}

function deepEvidenceInto(box, j) {
  const kv = el('div', 'dkv');
  const kvRow = (k, v) => {
    if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) return;
    kv.appendChild(el('span', 'k', k));
    kv.appendChild(el('span', 'v', Array.isArray(v) ? v.map(x => (typeof x === 'object' ? JSON.stringify(x) : x)).join(', ') : String(v)));
  };
  kvRow('trust basis', j.trust_basis);
  kvRow('published verdict', j.published_verdict);
  kvRow('verdict gate', j.verdict_gate);
  kvRow('contradiction', j.contradiction ? 'yes' : '');
  kvRow('IR required', j.required_ir_fingerprint);
  kvRow('excluded (other IR)', j.excluded_ir_mismatch);
  kvRow('unknown ids', j.unknown_ids);
  kvRow('budget refusals', Array.isArray(j.budget_refusals) ? j.budget_refusals.length || '' : '');
  kvRow('trust source', j.trust_source);
  box.appendChild(kv);
  const rows = deepEvidenceRows(j);
  if (!rows.length) {
    box.appendChild(el('div', 'dnote', 'no evidence recorded'));
  } else {
    const t = el('table', 'dtable');
    const hr = el('tr');
    ['id', 'tool', 'trust', 'ok', 'cited by', 'reason'].forEach(h => hr.appendChild(el('th', null, h)));
    const thead = el('thead');
    thead.appendChild(hr);
    t.appendChild(thead);
    const tb = el('tbody');
    rows.forEach(r => {
      const tr = el('tr', r.basis ? 'basis' : null);
      const idc = el('td', 'id', r.id);
      if (r.basis) idc.appendChild(el('span', 'tag', 'basis'));
      if (r.refuted) idc.appendChild(el('span', 'tag warn', 'refuted'));
      if (r.missing) idc.appendChild(el('span', 'tag warn', 'unknown'));
      tr.appendChild(idc);
      tr.appendChild(el('td', null, [r.tool, r.role ? `(${r.role})` : ''].filter(Boolean).join(' ')));
      const tc = el('td');
      if (r.trust) tc.appendChild(trustChip(r.trust));
      tr.appendChild(tc);
      tr.appendChild(el('td', r.ok ? 'ok-mark' : 'fail-mark', r.ok ? '✓' : '✗'));
      tr.appendChild(el('td', null, r.steps.length ? 'step ' + r.steps.join(', ') : ''));
      const reason = [r.reason, r.bounds ? 'bounds ' + JSON.stringify(r.bounds) : '', r.ir ? r.ir : ''].filter(Boolean).join(' · ');
      tr.appendChild(el('td', 'reason', reason));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    box.appendChild(t);
  }
  const steps = Array.isArray(j.steps) ? j.steps : [];
  if (steps.length) {
    box.appendChild(el('div', 'dsec-title', 'steps'));
    const t = el('table', 'dtable');
    const tb = el('tbody');
    steps.forEach((s, i) => {
      const tr = el('tr');
      tr.appendChild(el('td', 'id', String(i + 1)));
      const tc = el('td');
      tc.appendChild(trustChip(s.trust, (s.trust_reasons || []).join('; ')));
      tr.appendChild(tc);
      tr.appendChild(el('td', 'reason', String(s.claim || '')));
      tr.appendChild(el('td', null, (s.evidence_ids || []).join(', ')));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    box.appendChild(t);
  }
}

// result.md -> sanitized HTML (the same marked + DOMPurify + KaTeX path as the pipelines' Markdown tab)
async function deepRenderMarkdownInto(target, rawMd) {
  if (!rawMd || !rawMd.trim()) { target.textContent = '—'; return; }
  try {
    await ensureMdLibs();
  } catch (e) {
    target.textContent = '';
    target.appendChild(el('pre', 'dpre', rawMd));
    return;
  }
  window.marked.setOptions({gfm: true, breaks: false, headerIds: false, mangle: false});
  // result.md is agent-generated: always sanitized before innerHTML
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
// Deep-agent settings (GET / PUT /api/deep/settings)
//---------------------------------------------------------------------------
const DEEP_SET_NUMBERS = {
  budget: [['per_task_usd', 'per task, $', 0.01], ['per_day_usd', 'per day, $', 0.01],
    ['per_subagent_max_usd', 'per subagent max, $', 0.01], ['reserve_for_report_usd', 'reserve for the report, $', 0.01]],
  limits: [['max_model_calls', 'max model calls', 1], ['max_tool_calls', 'max tool calls', 1],
    ['max_parallel', 'max parallel', 1], ['max_spawn_depth', 'max spawn depth', 1]],
  intake: [['max_pdf_pages', 'max PDF pages', 1], ['max_file_mb', 'max file, MB', 0.5]],
};
const DEEP_KNOWN_MODELS = ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5'];
let deepSetForm = null;

function deepCloseSettings() { deepSettingsBack.classList.remove('show'); }
async function deepOpenSettings() {
  deepSettingsBack.classList.add('show');
  deepSettingsStatus.textContent = '';
  deepSettingsStatus.classList.remove('err');
  deepSettingsBody.textContent = '';
  deepSettingsBody.appendChild(el('span', 'hint', 'loading…'));
  try {
    const d = await api('/deep/settings');
    deepBuildSettingsForm(d);
  } catch (e) {
    deepSettingsBody.textContent = '';
    deepSettingsBody.appendChild(el('span', 'hint', 'could not load the settings — ' + e.message));
  }
}
function deepBuildSettingsForm(d) {
  const s = d.settings || {};
  deepSettingsBody.textContent = '';
  const form = {numbers: [], allowed: [], roles: [], flags: []};
  deepSetForm = form;
  if (d.error) deepSettingsBody.appendChild(el('div', 'dbanner err', 'the settings file is invalid (shown: the defaults): ' + d.error));
  deepSettingsBody.appendChild(el('span', 'hint', `file: ${d.path || '?'} · money limits apply to live runs; mock runs spend nothing`));
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
  const gb = group('budget');
  DEEP_SET_NUMBERS.budget.forEach(([k, l, st]) => numField(gb, 'budget', k, l, st));
  const models = s.models || {};
  const gm = group('models');
  const allowedNow = Array.isArray(models.allowed) ? models.allowed : [];
  const all = Array.from(new Set([...DEEP_KNOWN_MODELS, ...allowedNow, ...Object.values(models.roles || {})]));
  all.forEach(m => {
    const lab = el('label', 'check');
    const inp = document.createElement('input');
    inp.type = 'checkbox';
    inp.checked = allowedNow.includes(m);
    lab.appendChild(inp);
    lab.appendChild(el('span', null, 'allowed: ' + m));
    gm.appendChild(lab);
    form.allowed.push({model: m, inp});
  });
  Object.entries(models.roles || {}).forEach(([role, m]) => {
    const f = el('div', 'field');
    const sel = deepSelect(all, m);
    sel.id = 'dset-role-' + role;
    const lab = el('label', null, 'role ' + role);
    lab.htmlFor = sel.id;
    f.appendChild(lab);
    f.appendChild(sel);
    gm.appendChild(f);
    form.roles.push({role, sel});
  });
  flag(gm, 'models', 'opus_needs_reason', 'Opus needs a reason (logged)');
  const gh = group('human review (HITL)');
  flag(gh, 'hitl', 'formalization', 'HITL 1: transcription + IR');
  flag(gh, 'hitl', 'report', 'HITL 2: the report');
  flag(gh, 'hitl', 'execute', 'every sandbox execute');
  const gl = group('limits');
  DEEP_SET_NUMBERS.limits.forEach(([k, l, st]) => numField(gl, 'limits', k, l, st));
  const gi = group('intake');
  DEEP_SET_NUMBERS.intake.forEach(([k, l, st]) => numField(gi, 'intake', k, l, st));
}
async function deepSaveSettings() {
  const form = deepSetForm;
  if (!form) return;
  const out = {budget: {}, models: {}, limits: {}, hitl: {}, intake: {}};
  for (const n of form.numbers) {
    const v = Number(n.inp.value);
    if (n.inp.value === '' || !Number.isFinite(v)) {
      deepSettingsStatus.textContent = `${n.section}.${n.key} must be a number`;
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
  deepSettingsStatus.textContent = 'saving…';
  try {
    const d = await sendJson('/deep/settings', out, 'PUT');
    deepBuildSettingsForm(d);
    deepSettingsStatus.textContent = 'saved';
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

//---------------------------------------------------------------------------
// Deep Agent bootstrap: the tab chosen last (or #deep in the URL)
//---------------------------------------------------------------------------
(function initMode() {
  let mode = location.hash === '#deep' ? 'deep' : null;
  if (!mode) { try { mode = localStorage.getItem('tfl-lab-mode'); } catch (e) { mode = null; } }
  deepRenderAll();
  if (mode === 'deep') setMode('deep');
})();
