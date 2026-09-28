//---------------------------------------------------------------------------
// App state
//---------------------------------------------------------------------------
const state = {
  projects: [],
  project: null,
  examples: [],
  exampleName: null,
  runId: null,
  running: false,
  logPollTimer: null,
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
  exampleCount.textContent = '…';
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
  exampleCount.textContent = state.examples.length;
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
  } catch (e) {
    setStatus('error', `failed to load ${name}: ${e.message}`);
  }
}

//---------------------------------------------------------------------------
// Live-run confirmation gating
//---------------------------------------------------------------------------
confirmSpend.addEventListener('change', () => {
  btnLive.disabled = !confirmSpend.checked;
  liveGroup.classList.toggle('armed', confirmSpend.checked);
});

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
  state.running = true;
  btnMock.disabled = true; btnLive.disabled = true;
  btnCancel.style.display = ''; btnCancel.disabled = false;
  const label = live ? 'live run' : 'mock run';
  const t0 = performance.now();
  setStatus('running', `${label}: dispatching to backend…`);
  clearResults();
  try {
    const r = await fetch(`${API}/run`, {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({
        project: state.project,
        ir, live: !!live, verbose: verbose.checked
      }),
    });
    if (!r.ok) {
      const err = await r.text();
      throw new Error(`${r.status}: ${err}`);
    }
    const data = await r.json();
    state.runId = data.run_id;
    setStatus('running', `${label}: run ${data.run_id} — polling…`);
    startLogPolling(data);
    if (data.result_html_url) loadResult(data);
  } catch (e) {
    const dt = ((performance.now() - t0) / 1000).toFixed(1);
    setStatus('error', `${label} failed after ${dt}s — ${e.message}`);
    state.running = false;
    btnMock.disabled = false;
    btnLive.disabled = !confirmSpend.checked;
    btnCancel.style.display = 'none';
  }
}

async function cancelRun() {
  if (!state.runId) return;
  btnCancel.disabled = true;
  try {
    await fetch(`${API}/runs/${state.runId}/cancel`, {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: '{}',
    });
    // The next log-poll tick picks up the final "cancelled" status.
  } catch (e) { /* transient — polling still reflects the real status */ }
}

function loadResult(data) {
  if (data.result_html_url) {
    htmlEmpty.style.display = 'none';
    resultIframe.style.display = 'block';
    resultIframe.src = data.result_html_url;
    dlHtml.href = data.result_html_url;
    dlHtml.classList.remove('disabled');
  }
  if (data.result_json_url) {
    dlJson.href = data.result_json_url;
    dlJson.classList.remove('disabled');
    fetch(data.result_json_url).then(r => r.text()).then(t => {
      try {
        jsonPanel.textContent = JSON.stringify(JSON.parse(t), null, 2);
      } catch { jsonPanel.textContent = t; }
      jsonPanel.classList.remove('empty');
    }).catch(() => {});
  }
  if (data.result_md_url) {
    fetch(data.result_md_url).then(r => r.text()).then(t => {
      state.mdSource = t;
      mdRawPanel.textContent = t;
      mdRawPanel.classList.remove('empty');
      renderMarkdown(t);
    }).catch(() => {});
  }
}

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

function startLogPolling(runData) {
  if (state.logPollTimer) clearInterval(state.logPollTimer);
  const url = runData.log_url || `${API}/log/${state.runId}`;
  let lastLen = 0;
  const tick = async () => {
    try {
      const r = await fetch(url);
      if (!r.ok) return;
      const data = await r.json();
      const lines = data.lines || [];
      renderLog(lines);
      logBadge.textContent = lines.length;
      const TERMINAL = ['completed', 'error', 'timeout', 'cancelled'];
      if (TERMINAL.includes(data.status)) {
        clearInterval(state.logPollTimer);
        state.logPollTimer = null;
        state.running = false;
        btnMock.disabled = false;
        btnLive.disabled = !confirmSpend.checked;
        btnCancel.style.display = 'none';
        const verdict = data.verdict || data.result?.verdict;
        if (data.status === 'error') {
          setStatus('error', `run ${state.runId} errored` + (data.error ? ` — ${data.error}` : ''));
        } else if (data.status === 'timeout') {
          setStatus('error', `run ${state.runId} timed out` + (data.error ? ` — ${data.error}` : ''));
        } else if (data.status === 'cancelled') {
          setStatus('error', `run ${state.runId} cancelled`);
        } else {
          setStatus('success', `done · ${data.elapsed ? data.elapsed.toFixed(1) + 's' : '—'} · verdict: ${verdict || '—'}`);
        }
        if (data.result_html_url || data.result_json_url) loadResult(data);
      }
    } catch (e) { /* ignore transient */ }
  };
  tick();
  state.logPollTimer = setInterval(tick, 600);
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

function clearResults() {
  htmlEmpty.style.display = 'flex';
  htmlEmpty.textContent = 'running…';
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
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === t));
    const target = t.dataset.tab;
    document.querySelectorAll('.panel').forEach(p =>
      p.classList.toggle('active', p.dataset.panel === target));
  });
});

//---------------------------------------------------------------------------
// Wire buttons
//---------------------------------------------------------------------------
btnMock.addEventListener('click', () => runPipeline(false));
btnLive.addEventListener('click', () => runPipeline(true));
btnCancel.addEventListener('click', () => cancelRun());
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
loadProjects();
setStatus(null, 'ready');
