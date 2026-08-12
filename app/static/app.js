import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/jsm/controls/OrbitControls.js';
import { GLTFLoader } from '/static/vendor/jsm/loaders/GLTFLoader.js';
import { Sky } from '/static/vendor/jsm/objects/Sky.js';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElementNS(
    tag === 'svg' || SVG_TAGS.has(tag) ? 'http://www.w3.org/2000/svg' : 'http://www.w3.org/1999/xhtml',
    tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.setAttribute('class', v);
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const k of kids.flat()) n.append(k?.nodeType ? k : document.createTextNode(k));
  return n;
};
const SVG_TAGS = new Set(['svg', 'g', 'rect', 'line', 'circle', 'text', 'path']);

const S = { job: null, sheetId: null, sel: null, cal: null, notes: [] };

// ─────────────────────────────────────────────────────────── units
const ftIn = (ft) => {
  const neg = ft < 0; ft = Math.abs(ft);
  let w = Math.floor(ft + 1e-9), i = Math.round((ft - w) * 12);
  if (i === 12) { w += 1; i = 0; }
  return (neg ? '−' : '') + (i ? `${w}′-${i}″` : `${w}′`);
};

// ─────────────────────────────────────────────────────────── api
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const ct = r.headers.get('content-type') || '';
  const body = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body?.error?.message || r.statusText);
  return body;
}
const jput = (path, data) =>
  api(path, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(data) });
const jpost = (path, data) =>
  api(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: data ? JSON.stringify(data) : null });

// ─────────────────────────────────────────────────────────── steps
function step(name) {
  $$('.step').forEach(s => s.classList.toggle('on', s.id === 'step-' + name));
  $$('#steps button').forEach(b => b.classList.toggle('on', b.dataset.step === name));
  if (name === 'model') sizeViewer();
  if (name === 'facade') loadFacade();
}
$$('#steps button').forEach(b => b.addEventListener('click', () => { if (!b.disabled) step(b.dataset.step); }));
function unlock(...names) {
  $$('#steps button').forEach(b => { if (names.includes(b.dataset.step)) b.disabled = false; });
}

// ─────────────────────────────────────────────────────────── upload
const drop = $('#drop');
['dragenter', 'dragover'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.add('hot');
}));
['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.remove('hot');
}));
drop.addEventListener('drop', ev => upload([...ev.dataTransfer.files]));
$('#filepick').addEventListener('change', ev => upload([...ev.target.files]));
$('#newjob').addEventListener('click', () => location.reload());

async function upload(files) {
  files = files.filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (!files.length) return fail('Those are not PDFs.');
  $('#uploaderr').hidden = true;
  $('#uploading').hidden = false;
  const fd = new FormData();
  files.forEach(f => fd.append('files', f, f.name));
  try {
    const res = await api('/api/jobs', { method: 'POST', body: fd });
    S.notes = res.notes || [];
    setJob(res.job);
    step('sheets');
  } catch (e) { fail(e.message); }
  finally { $('#uploading').hidden = true; }
}
function fail(msg) { const e = $('#uploaderr'); e.textContent = msg; e.hidden = false; }

function setJob(job) {
  S.job = job;
  $('#jobbar').hidden = false;
  $('#jobtitle').textContent =
    `${job.title || 'Untitled project'} · ${job.sheets.length} sheets · set ${job.id}`;
  const storeys = Object.keys(job.extracts);
  unlock('sheets');
  if (storeys.length) unlock('check', 'heights', 'finish', 'facade', 'model');
  if (!S.sheetId || !job.extracts[S.sheetId]) S.sheetId = storeys[0] || null;
  renderSheets();
  renderCheck();
  renderHeights();
  renderFinish();
}

// ─────────────────────────────────────────────────────────── 2 · sheets
function renderSheets() {
  const nd = $('#notes'); nd.textContent = '';
  S.notes.forEach(n => nd.append(el('div', { class: 'note' }, n)));

  const t = $('#sheettable'); t.textContent = '';
  t.append(el('tr', {},
    el('th', {}, 'Sheet'), el('th', {}, 'Read as'), el('th', {}, 'Storey'), el('th', {}, 'Use as a storey')));
  for (const s of S.job.sheets) {
    const levels = [['', '—'], ['-1', 'Basement'], ['0', 'Ground floor'], ['1', 'First floor'],
                    ['2', 'Second floor'], ['3', 'Third floor'], ['4', 'Fourth floor']];
    const sel = el('select', {
      onchange: async (e) => patchSheet(s.id, { level: e.target.value === '' ? null : +e.target.value })
    }, ...levels.map(([v, lab]) => {
      const o = el('option', { value: v }, lab);
      if (String(s.level ?? '') === v) o.selected = true;
      return o;
    }));
    const inc = el('input', {
      type: 'checkbox', onchange: (e) => patchSheet(s.id, { include: e.target.checked })
    });
    inc.checked = s.include;
    if (s.level === null || s.level === undefined) inc.disabled = true;
    t.append(el('tr', { class: s.include ? 'inc' : '' },
      el('td', {}, el('div', { class: 'fname' }, s.filename),
        el('div', { class: 'ev' }, `${s.n_segments} lines · ${s.n_words} labels`)),
      el('td', {}, s.kind_label, el('div', { class: 'ev' }, s.evidence)),
      el('td', {}, sel),
      el('td', {}, inc)));
  }
}

async function patchSheet(id, patch) {
  try {
    const res = await jput(`/api/jobs/${S.job.id}/sheets`, [{ id, ...patch }]);
    S.notes = res.notes || [];
    setJob(res.job);
  } catch (e) { alert(e.message); renderSheets(); }
}
$('#tocheck').addEventListener('click', () => step('check'));
$('#toheights').addEventListener('click', () => step('heights'));
$('#tofinish').addEventListener('click', () => step('finish'));

// ─────────────────────────────────────────────────────────── 3 · check
const layer = (id) => $('#lay-' + id).checked;
$$('.layer-toggles input').forEach(i => i.addEventListener('change', drawPlan));

function renderCheck() {
  const tabs = $('#storeytabs'); tabs.textContent = '';
  const ids = Object.keys(S.job.extracts);
  if (!ids.length) { $('#planinner').textContent = ''; return; }
  for (const id of ids) {
    const ex = S.job.extracts[id];
    const b = el('button', { class: id === S.sheetId ? 'on' : '', onclick: () => { S.sheetId = id; S.sel = null; renderCheck(); } }, ex.level_name);
    tabs.append(b);
  }
  drawPlan();
  drawScaleCard();
  drawStats();
  drawSelection();
}

function currentExtract() { return S.job.extracts[S.sheetId]; }
function currentSheet() { return S.job.sheets.find(s => s.id === S.sheetId); }

function drawPlan() {
  const ex = currentExtract(), sh = currentSheet();
  if (!ex || !sh) return;
  const wrap = $('#planinner');
  wrap.textContent = '';
  wrap.classList.toggle('hide-plan', !layer('plan'));

  const img = el('img', { src: `/api/jobs/${S.job.id}/sheets/${S.sheetId}/plan.png?dpi=150`, alt: 'plan sheet' });
  wrap.append(img);

  const [ox, oy] = ex.origin_px, px = ex.scale.px_per_ft;
  const X = (f) => ox + f * px, Y = (f) => oy + f * px;
  const svg = el('svg', {
    viewBox: `0 0 ${sh.page_width} ${sh.page_height}`, preserveAspectRatio: 'none'
  });

  if (layer('rooms')) for (const r of ex.rooms) {
    svg.append(el('rect', { class: 'room', x: X(r.x0), y: Y(r.y0), width: (r.x1 - r.x0) * px, height: (r.y1 - r.y0) * px }));
  }
  if (layer('cols')) for (const c of ex.columns) {
    svg.append(el('rect', { class: 'col', x: X(c.x0), y: Y(c.y0), width: (c.x1 - c.x0) * px, height: (c.y1 - c.y0) * px }));
  }
  if (layer('walls')) for (const w of ex.walls) {
    const railing = w.kind === 'railing';
    const len = w.axis === 'h' ? w.x1 - w.x0 : w.y1 - w.y0;
    const thick = w.axis === 'h' ? w.y1 - w.y0 : w.x1 - w.x0;
    const r = el('rect', {
      class: railing ? 'rail' : 'wall' + (w.exterior ? ' ext' : ''),
      x: X(w.x0), y: Y(w.y0), width: (w.x1 - w.x0) * px, height: (w.y1 - w.y0) * px
    });
    r.append(el('title', {}, `${w.id} · ${railing ? 'railing' : 'wall'} · ${ftIn(len)} long · ${Math.round(thick * 12)}″ thick`));
    svg.append(r);
    if (railing) continue;
    // clickable strip for adding an opening
    const hit = el('rect', {
      class: 'hit', x: X(w.x0), y: Y(w.y0), width: (w.x1 - w.x0) * px, height: (w.y1 - w.y0) * px,
      onclick: (e) => addOpening(w, e)
    });
    svg.append(hit);
  }
  if (layer('open')) for (const w of ex.walls) for (const o of w.openings) {
    const sel = S.sel && S.sel.wall === w.id && S.sel.op === o.id;
    const pad = 1.6;
    const box = w.axis === 'h'
      ? { x: X(o.u0), y: Y(w.y0) - pad, width: (o.u1 - o.u0) * px, height: (w.y1 - w.y0) * px + 2 * pad }
      : { x: X(w.x0) - pad, y: Y(o.u0), width: (w.x1 - w.x0) * px + 2 * pad, height: (o.u1 - o.u0) * px };
    const r = el('rect', {
      class: `op ${o.kind}${sel ? ' sel' : ''}`, ...box,
      onclick: (e) => { e.stopPropagation(); S.sel = { wall: w.id, op: o.id }; drawPlan(); drawSelection(); }
    });
    r.append(el('title', {}, `${o.kind} · ${ftIn(Math.abs(o.u1 - o.u0))} wide${o.source === 'manual' ? ' · edited' : ''}`));
    svg.append(r);
  }

  if (S.cal) {
    for (const p of S.cal.pts) svg.append(el('circle', { class: 'calpt', cx: X(p[0]), cy: Y(p[1]), r: 2.4 }));
    if (S.cal.pts.length === 2) svg.append(el('line', {
      class: 'calline', x1: X(S.cal.pts[0][0]), y1: Y(S.cal.pts[0][1]),
      x2: X(S.cal.pts[1][0]), y2: Y(S.cal.pts[1][1])
    }));
    svg.style.cursor = 'crosshair';
    svg.addEventListener('click', (e) => calClick(e, svg, ox, oy, px));
  }
  wrap.append(svg);
}

function svgPoint(e, svg) {
  const r = svg.getBoundingClientRect();
  const vb = svg.viewBox.baseVal;
  return [vb.x + (e.clientX - r.left) / r.width * vb.width,
          vb.y + (e.clientY - r.top) / r.height * vb.height];
}

async function addOpening(w, e) {
  if (S.cal) return;
  const ex = currentExtract();
  const svg = e.target.ownerSVGElement;
  const [px_, py_] = svgPoint(e, svg);
  const [ox, oy] = ex.origin_px, px = ex.scale.px_per_ft;
  const u = w.axis === 'h' ? (px_ - ox) / px : (py_ - oy) / px;
  const kind = e.shiftKey ? 'window' : 'door';
  const width = kind === 'door' ? 3 : 4;
  try {
    const res = await jput(`/api/jobs/${S.job.id}/sheets/${S.sheetId}/openings`,
      [{ wall_id: w.id, u0: u - width / 2, u1: u + width / 2, kind }]);
    S.job.extracts[S.sheetId] = res.extract;
    const wall = res.extract.walls.find(x => x.id === w.id);
    S.sel = { wall: w.id, op: wall.openings[wall.openings.length - 1].id };
    S.job.build = null;
    drawPlan(); drawSelection(); drawStats();
  } catch (err) { alert(err.message); }
}

function drawSelection() {
  const card = $('#selcard'); card.textContent = '';
  const ex = currentExtract();
  if (!S.sel) {
    card.append(el('h3', {}, 'Nothing selected'),
      el('p', { class: 'muted small' }, 'Click an opening to edit it. Click a bare wall to add a door — hold shift for a window.'));
    return;
  }
  const w = ex.walls.find(x => x.id === S.sel.wall);
  const o = w?.openings.find(x => x.id === S.sel.op);
  if (!o) { S.sel = null; return drawSelection(); }
  const lp = (S.job.params.levels || []).find(l => l.level === ex.level) || {};

  const kindSel = el('select', { onchange: e => saveOpening({ kind: e.target.value }) },
    ...['door', 'window'].map(k => {
      const opt = el('option', { value: k }, k); if (o.kind === k) opt.selected = true; return opt;
    }));
  const widthIn = el('input', { type: 'number', step: '0.25', min: '0.25', value: (o.u1 - o.u0).toFixed(2) });
  widthIn.addEventListener('change', () => {
    const c = (o.u0 + o.u1) / 2, wd = Math.max(0.25, +widthIn.value);
    saveOpening({ u0: c - wd / 2, u1: c + wd / 2 });
  });
  const sillIn = el('input', { type: 'number', step: '0.25', value: o.sill_ft ?? (o.kind === 'door' ? 0 : lp.window_sill_ft ?? 3) });
  const headIn = el('input', { type: 'number', step: '0.25', value: o.head_ft ?? (o.kind === 'door' ? lp.door_head_ft ?? 7 : lp.window_head_ft ?? 7) });
  sillIn.addEventListener('change', () => saveOpening({ sill_ft: +sillIn.value }));
  headIn.addEventListener('change', () => saveOpening({ head_ft: +headIn.value }));

  card.append(
    el('h3', {}, `${o.kind} on ${w.id}`),
    el('div', { class: 'field' }, el('span', {}, 'Type'), kindSel),
    el('div', { class: 'field' }, el('span', {}, 'Width (ft)'), widthIn),
    el('div', { class: 'field' }, el('span', {}, 'Sill (ft)'), sillIn),
    el('div', { class: 'field' }, el('span', {}, 'Head (ft)'), headIn),
    el('p', { class: 'muted small' }, `${ftIn(Math.abs(o.u1 - o.u0))} wide · ${o.source === 'auto' ? 'read from the drawing' : 'edited by hand'}`),
    el('button', { class: 'btn ghost sm', onclick: () => saveOpening({ delete: true }) }, 'Delete opening'));
}

async function saveOpening(patch) {
  const { wall, op } = S.sel;
  try {
    const res = await jput(`/api/jobs/${S.job.id}/sheets/${S.sheetId}/openings`,
      [{ wall_id: wall, opening_id: op, ...patch }]);
    S.job.extracts[S.sheetId] = res.extract;
    S.job.build = null;
    if (patch.delete) S.sel = null;
    drawPlan(); drawSelection(); drawStats();
  } catch (e) { alert(e.message); }
}

function drawScaleCard() {
  const ex = currentExtract(), card = $('#scalecard');
  card.textContent = '';
  const s = ex.scale;
  card.append(el('h3', {}, 'Scale'),
    el('p', {}, el('b', {}, `${s.px_per_ft.toFixed(3)} pt per foot `),
      el('span', { class: `badge ${s.confidence}` }, s.confidence)),
    el('p', { class: 'muted small' }, s.note || s.method));

  if (!S.cal) {
    card.append(el('button', {
      class: 'btn ghost sm', onclick: () => { S.cal = { pts: [] }; drawScaleCard(); drawPlan(); }
    }, 'Calibrate by hand'));
  } else {
    const n = S.cal.pts.length;
    card.append(el('p', { class: 'small' },
      n < 2 ? `Click ${2 - n} more point${n === 1 ? '' : 's'} on a known dimension.` : 'Now type the real distance.'));
    if (n === 2) {
      const inp = el('input', { type: 'text', placeholder: `e.g. 30' or 12'-6"` });
      const go = el('button', { class: 'btn sm', onclick: () => applyCal(inp.value) }, 'Set scale');
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') applyCal(inp.value); });
      card.append(el('div', { class: 'field' }, inp, go));
    }
    card.append(el('button', { class: 'btn ghost sm', onclick: () => { S.cal = null; drawScaleCard(); drawPlan(); } }, 'Cancel'));
  }
}

function calClick(e, svg, ox, oy, px) {
  if (!S.cal || S.cal.pts.length >= 2) return;
  const [x, y] = svgPoint(e, svg);
  S.cal.pts.push([(x - ox) / px, (y - oy) / px]);
  drawPlan(); drawScaleCard();
}

function parseFeet(text) {
  const m = String(text).match(/(?:(\d+(?:\.\d+)?)\s*['’])?\s*-?\s*(?:(\d+(?:\.\d+)?)\s*["”])?/);
  if (!m) return null;
  const ft = m[1] ? parseFloat(m[1]) : 0, inch = m[2] ? parseFloat(m[2]) : 0;
  const plain = parseFloat(text);
  if (!m[1] && !m[2]) return isNaN(plain) ? null : plain;
  return ft + inch / 12;
}

async function applyCal(text) {
  const len = parseFeet(text);
  if (!len || len <= 0) return alert('Give a length like 30\' or 12\'-6"');
  try {
    const res = await jput(`/api/jobs/${S.job.id}/sheets/${S.sheetId}/scale`,
      { p0: S.cal.pts[0], p1: S.cal.pts[1], length_ft: len });
    S.job.extracts[S.sheetId] = res.extract;
    S.job.build = null; S.cal = null; S.sel = null;
    renderCheck();
  } catch (e) { alert(e.message); }
}

$('#resetsheet').addEventListener('click', async () => {
  const res = await jpost(`/api/jobs/${S.job.id}/sheets/${S.sheetId}/reset`);
  S.job.extracts[S.sheetId] = res.extract;
  S.sel = null; S.cal = null; S.job.build = null;
  renderCheck();
});

function drawStats() {
  const ex = currentExtract(), card = $('#statscard');
  const doors = ex.walls.reduce((n, w) => n + w.openings.filter(o => o.kind === 'door').length, 0);
  const wins = ex.walls.reduce((n, w) => n + w.openings.filter(o => o.kind === 'window').length, 0);
  const walls = ex.walls.filter(w => w.kind !== 'railing');
  const rails = ex.walls.length - walls.length;
  const ext = walls.filter(w => w.exterior).length;
  card.textContent = '';
  card.append(el('h3', {}, 'What was read'),
    el('dl', {},
      el('dt', {}, 'Walls'), el('dd', {}, `${walls.length} (${ext} external)`),
      el('dt', {}, 'Railings'), el('dd', {}, String(rails)),
      el('dt', {}, 'Doorways'), el('dd', {}, String(doors)),
      el('dt', {}, 'Windows'), el('dd', {}, String(wins)),
      el('dt', {}, 'Columns'), el('dd', {}, String(ex.columns.length)),
      el('dt', {}, 'Rooms measured'), el('dd', {}, String(ex.rooms.length)),
      el('dt', {}, 'Extent'), el('dd', {}, `${ftIn(ex.bounds[2] - ex.bounds[0])} × ${ftIn(ex.bounds[3] - ex.bounds[1])}`)));
  const rooms = ex.rooms.filter(r => r.label_ft);
  if (rooms.length) {
    const d = el('details', {}, el('summary', {}, 'Room labels against measurements'));
    const list = el('dl', {});
    for (const r of rooms) {
      list.append(el('dt', {}, r.name),
        el('dd', {}, `${ftIn(r.label_ft[0])}×${ftIn(r.label_ft[1])} → ${ftIn(r.measured_ft[0])}×${ftIn(r.measured_ft[1])}`));
    }
    d.append(list); card.append(d);
  }
  if (ex.warnings?.length) {
    const d = el('details', {}, el('summary', {}, `${ex.warnings.length} notes from the extractor`));
    for (const w of ex.warnings) d.append(el('p', { class: 'muted small' }, '· ' + w));
    card.append(d);
  }
}

// ─────────────────────────────────────────────────────────── 4 · heights
// Where a number came from, shown as a pill beside its label. The server is
// what decides this — it diffs what the page sends against what it stored —
// so the page only ever displays the answer, never works it out.
const SRC = {
  measured: ['measured', 'read off a sheet in this set'],
  derived: ['derived', 'worked out from a schedule or a note'],
  user: ['yours', 'you typed this; no reading will overwrite it'],
  default: ['assumed', 'the tool’s default — nothing in the drawings says this'],
};

function provAt(path) {
  const p = S.job && S.job.params;
  if (!p) return null;
  const parts = path.split('.');
  if (parts[0] === 'building') return (p.provenance || {})[parts[1]];
  const lp = (p.levels || []).find(l => String(l.level) === parts[1]);
  return lp ? (lp.provenance || {})[parts[2]] : null;
}

function fillProv(field) {
  const host = field.querySelector('.badge-host');
  const alts = field.querySelector('.alts');
  if (host) host.textContent = '';
  if (alts) alts.textContent = '';
  const q = provAt(field.dataset.prov);
  if (!q) return;
  const [text, why] = SRC[q.source] || [q.source, ''];
  const tip = [why, q.method && `method: ${q.method}`, q.evidence,
    q.sheet_id && `sheet: ${q.sheet_id}`].filter(Boolean).join('\n');
  if (host) host.append(el('span', { class: `badge src src-${q.source}`, title: tip }, text));
  // a disagreement between two sheets is worth reading, so it gets its own line
  if (alts && q.alternatives && q.alternatives.length)
    alts.textContent = `also read ${q.alternatives.join('; ')}`;
}

// A save can change a number's story — one you typed becomes yours. Repaint
// only the pills, so a re-render never steals focus from a field being edited.
function refreshHeightBadges() {
  document.querySelectorAll('#heightsform .field[data-prov]').forEach(fillProv);
}

function renderHeights() {
  const f = $('#heightsform'); f.textContent = '';
  if (!S.job) return;
  const p = S.job.params;

  const num = (obj, key, label, hint) => {
    const v = obj[key];
    const i = el('input', {
      type: 'number', step: '0.25',
      value: typeof v === 'number' ? String(Math.round(v * 1000) / 1000) : (v ?? '')
    });
    i.addEventListener('change', () => { obj[key] = +i.value; saveParams(); });
    const host = el('span', { class: 'badge-host' });
    const alts = el('div', { class: 'ev alts' });
    const field = el('div', { class: 'field' },
      el('span', {}, label, ' ', host,
        hint ? el('div', { class: 'ev' }, hint) : '', alts),
      i);
    field.dataset.prov = obj === p ? `building.${key}` : `level.${obj.level}.${key}`;
    fillProv(field);
    return field;
  };

  for (const lp of p.levels) {
    const card = el('div', { class: 'lvl-card' }, el('h3', {}, lp.name));
    card.append(el('div', { class: 'grid2' },
      num(lp, 'floor_to_floor_ft', 'Floor to floor (ft)'),
      num(lp, 'window_sill_ft', 'Window sill (ft)'),
      num(lp, 'window_head_ft', 'Window head (ft)'),
      num(lp, 'door_head_ft', 'Door head (ft)'),
      num(lp, 'slab_thickness_ft', 'Slab (ft)')));
    f.append(card);
  }
  const g = el('div', { class: 'lvl-card' }, el('h3', {}, 'Whole building'));
  const roofSel = el('select', { onchange: e => { p.roof = e.target.value; saveParams(); } },
    ...[['flat_parapet', 'Flat roof with parapet'], ['flat', 'Flat roof, no parapet'], ['none', 'No roof']]
      .map(([v, l]) => { const o = el('option', { value: v }, l); if (p.roof === v) o.selected = true; return o; }));
  const chk = (key, label) => {
    const i = el('input', { type: 'checkbox' });
    i.checked = p[key];
    i.addEventListener('change', () => { p[key] = i.checked; saveParams(); });
    return el('div', { class: 'field' }, el('span', {}, label), i);
  };
  g.append(el('div', { class: 'grid2' },
    num(p, 'plinth_ft', 'Plinth above ground (ft)'),
    num(p, 'parapet_ft', 'Parapet height (ft)'),
    num(p, 'railing_ft', 'Railing height (ft)', 'balcony and stairwell guards'),
    el('div', { class: 'field' }, el('span', {}, 'Roof'), roofSel),
    num(p, 'roof_slab_thickness_ft', 'Roof slab (ft)', 'blank → the top storey’s slab'),
    chk('columns', 'Model the columns'),
    chk('glazing', 'Glazing in the windows'),
    chk('doors', 'Door leaves'),
    chk('ground', 'Ground plane'),
    chk('align_north', 'Rotate so north is −Z')));
  f.append(g);
}

let saveTimer = null;
function saveParams() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      const res = await jput(`/api/jobs/${S.job.id}/params`, S.job.params);
      S.job = res.job; S.job.build = null;
      refreshHeightBadges();
    } catch (e) { alert(e.message); }
  }, 250);
}

// ─────────────────────────────────────────── 5 · materials and site
let CATALOG = null;

async function loadCatalog() {
  if (!CATALOG) CATALOG = await api('/api/catalog');
  return CATALOG;
}

async function renderFinish() {
  if (!S.job) return;
  const cat = await loadCatalog();
  const p = S.job.params;
  p.finish = p.finish || { preset: 'plaster_stone', slots: {} };
  p.site = p.site || {};

  // presets
  const pv = $('#presets'); pv.textContent = '';
  for (const [key, meta] of Object.entries(cat.presets)) {
    const swatches = el('div', { class: 'sw' },
      ...['wall_ext', 'base', 'trim', 'door'].map(k =>
        el('span', { class: 'chip', style: `background:${meta.slots[k][0]}` })));
    pv.append(el('button', {
      class: 'preset' + (p.finish.preset === key ? ' on' : ''),
      onclick: () => { p.finish.preset = key; p.finish.slots = {}; saveParams(); renderFinish(); }
    }, el('b', {}, meta.label), swatches, el('span', { class: 'ev' }, meta.note)));
  }

  // per-surface colour + texture
  const t = $('#slottable'); t.textContent = '';
  t.append(el('tr', {}, el('th', {}, 'Surface'), el('th', {}, 'Colour'), el('th', {}, 'Finish')));
  const resolved = cat.presets[p.finish.preset]?.slots || {};
  for (const [key, label] of cat.slots) {
    const cur = p.finish.slots[key] || resolved[key] || ['#cccccc', null];
    const color = el('input', { type: 'color', value: cur[0] || '#cccccc' });
    color.addEventListener('input', () => {
      p.finish.slots[key] = [color.value, texSel.value === 'none' ? null : texSel.value];
      saveParams();
    });
    const options = cat.textures[key] || cat.textures.default;
    const texSel = el('select', {}, ...options.map(o => {
      const opt = el('option', { value: o }, o === 'none' ? 'flat colour' : o);
      if ((cur[1] || 'none') === o) opt.selected = true;
      return opt;
    }));
    texSel.addEventListener('change', () => {
      p.finish.slots[key] = [color.value, texSel.value === 'none' ? null : texSel.value];
      saveParams();
    });
    t.append(el('tr', {}, el('td', {}, label), el('td', {}, color), el('td', {}, texSel)));
  }

  // site
  const f = $('#siteform'); f.textContent = '';
  const num = (obj, key, label, step = '1') => {
    const i = el('input', { type: 'number', step, value: obj[key] ?? '' });
    i.addEventListener('change', () => { obj[key] = +i.value; saveParams(); });
    return el('div', { class: 'field' }, el('span', {}, label), i);
  };
  const chk = (obj, key, label) => {
    const i = el('input', { type: 'checkbox' });
    i.checked = !!obj[key];
    i.addEventListener('change', () => { obj[key] = i.checked; saveParams(); });
    return el('div', { class: 'field' }, el('span', {}, label), i);
  };
  f.append(
    chk(p.site, 'enabled', 'Show the site at all'),
    chk(p.site, 'boundary_wall', 'Boundary wall and gate'),
    num(p.site, 'boundary_height_ft', 'Boundary height (ft)', '0.5'),
    num(p.site, 'gate_width_ft', 'Gate width (ft)', '0.5'),
    num(p.site, 'front_setback_ft', 'Front setback (ft)', '1'),
    num(p.site, 'side_setback_ft', 'Side setback (ft)', '1'),
    chk(p.site, 'driveway', 'Driveway paving'),
    num(p.site, 'cars', 'Cars'),
    num(p.site, 'trees', 'Trees'),
    num(p.site, 'tree_height_ft', 'Tree height (ft)', '1'));
  f.append(el('p', { class: 'muted small' },
    'The gate and driveway face the side the drawing labels ROAD, and cars park in the room it labels as parking.'));
}


// ─────────────────────────────────────────────────────────── 6 · fasād
// The front as panels: a rectangle, a material, and a lvl saying how far it
// stands proud. Composed from the building, then editable — the elevation
// drawing and the model are both rebuilt from whatever the panels say.
let FACADE = null;

$('#tofacade').addEventListener('click', () => step('facade'));
$('#recompose').addEventListener('click', async () => {
  $('#facadeworking').hidden = false;
  try { FACADE = await jpost(`/api/jobs/${S.job.id}/facade/recompose`); drawFacade(); }
  catch (e) { alert(e.message); }
  finally { $('#facadeworking').hidden = true; }
});

async function loadFacade(force) {
  if (FACADE && !force) return drawFacade();
  $('#facadeworking').hidden = false;
  try {
    FACADE = await api(`/api/jobs/${S.job.id}/facade`);
    drawFacade();
  } catch (e) {
    $('#elevation').innerHTML = `<p class="muted">${e.message}</p>`;
  } finally { $('#facadeworking').hidden = true; }
}

async function saveFacade(patch) {
  const next = Object.assign({}, FACADE.params, patch);
  $('#facadeworking').hidden = false;
  try {
    FACADE = await jput(`/api/jobs/${S.job.id}/facade`, next);
    if (S.job) S.job.build = null;
    drawFacade();
  } catch (e) { alert(e.message); }
  finally { $('#facadeworking').hidden = true; }
}

const FT = v => {
  const neg = v < 0; v = Math.abs(v);
  const ft = Math.floor(v + 1e-6), inch = Math.round((v - ft) * 12);
  const s = inch ? `${ft}'-${inch}"` : `${ft}'`;
  return (neg ? '-' : '') + s;
};

function drawFacade() {
  if (!FACADE) return;
  $('#elevation').innerHTML = FACADE.svg;

  const p = FACADE.params;
  const f = $('#facadeform'); f.textContent = '';

  // the arrangement comes first: it decides what the rest of the controls are
  // adjusting, and it is the quickest way to a different-looking house
  const arr = el('div', { class: 'arrangements' });
  for (const [key, note] of Object.entries(FACADE.arrangements || {})) {
    const b = el('button', {
      class: 'arr' + (p.arrangement === key ? ' on' : ''),
      onclick: () => saveFacade({ arrangement: key }),
    }, el('strong', {}, key), el('div', { class: 'ev' }, note));
    arr.append(b);
  }
  f.append(arr);

  const sideSel = el('select', {
    onchange: e => saveFacade({ screen_side: e.target.value }),
  }, ...[['auto', 'Wherever there is room'], ['left', 'Left'], ['right', 'Right']]
      .map(([v, l]) => { const o = el('option', { value: v }, l);
        if (p.screen_side === v) o.selected = true; return o; }));
  f.append(el('div', { class: 'field' },
    el('span', {}, 'Screen stands', el('div', { class: 'ev' }, 'which end the fins go')),
    sideSel));
  const toggle = (key, label, hint) => {
    const i = el('input', { type: 'checkbox' });
    i.checked = !!p[key];
    i.addEventListener('change', () => saveFacade({ [key]: i.checked }));
    return el('div', { class: 'field' },
      el('span', {}, label, hint ? el('div', { class: 'ev' }, hint) : ''), i);
  };
  const num = (key, label, hint) => {
    const i = el('input', { type: 'number', step: '0.05', value: String(p[key] ?? '') });
    i.addEventListener('change', () => saveFacade({ [key]: +i.value }));
    return el('div', { class: 'field' },
      el('span', {}, label, hint ? el('div', { class: 'ev' }, hint) : ''), i);
  };
  f.append(el('div', { class: 'grid2' },
    toggle('canopy', 'Roof canopy', 'the deep dark slab across the top'),
    num('canopy_projection_ft', 'Canopy lvl (ft)'),
    toggle('fins', 'Fin screen', 'full height, past the canopy'),
    num('fin_pitch_ft', 'Fin spacing (ft)'),
    num('screen_width_ft', 'Screen width (ft)'),
    toggle('mass', 'Projecting clad bay'),
    num('mass_projection_ft', 'Bay lvl (ft)'),
    toggle('void', 'Recessed balcony'),
    num('void_depth_ft', 'Recess depth (ft)'),
    toggle('box_frames', 'Box frames', 'a surround standing proud of each opening'),
    num('frame_margin_ft', 'Frame width (ft)'),
    toggle('bands', 'Floor bands'),
    num('band_height_ft', 'Band height (ft)'),
    toggle('clad_panel', 'Feature cladding')));

  const t = $('#paneltable'); t.textContent = '';
  t.append(el('tr', {},
    el('th', {}, 'Panel'), el('th', {}, 'Size'), el('th', {}, 'lvl')));
  for (const panel of FACADE.panels) {
    const lvl = el('input', {
      type: 'number', step: '0.05', value: String(panel.depth_ft),
      style: 'width:74px'
    });
    lvl.addEventListener('change', () => {
      panel.depth_ft = +lvl.value;
      saveFacade({ panels: FACADE.panels });
    });
    t.append(el('tr', {},
      el('td', {}, el('div', {}, panel.label || panel.kind),
        el('div', { class: 'ev' }, panel.kind)),
      el('td', { class: 'ev' }, `${FT(panel.u1 - panel.u0)} × ${FT(panel.z1 - panel.z0)}`),
      el('td', {}, lvl)));
  }
  $('#facadecount').textContent =
    `${FACADE.panels.length} panels · front is ${FT(FACADE.frame.width_ft)} wide`;
}

// ─────────────────────────────────────────── 7 · build + viewer
$('#tobuild').addEventListener('click', async () => {
  step('model');
  await buildModel();
});

async function buildModel() {
  $('#building').hidden = false;
  try {
    const res = await jpost(`/api/jobs/${S.job.id}/build`);
    S.job.build = res.summary;
    drawSummary(res.summary);
    drawDownloads();
    await loadModel();
  } catch (e) { alert(e.message); }
  finally { $('#building').hidden = true; }
}

function drawSummary(s) {
  const c = $('#buildsummary'); c.textContent = '';
  c.append(el('h3', {}, 'The model'));
  const dl = el('dl', {});
  for (const lv of s.levels) {
    dl.append(el('dt', {}, lv.name),
      el('dd', {}, `floor ${ftIn(lv.floor_elevation_ft)} · ${lv.area_sqft.toFixed(0)} sq ft`));
  }
  dl.append(el('dt', {}, 'Height'), el('dd', {}, ftIn(s.overall_height_ft)));
  dl.append(el('dt', {}, 'Triangles'), el('dd', {}, String(s.triangles)));
  if (s.size_m) dl.append(el('dt', {}, 'Size'), el('dd', {}, `${s.size_m[0].toFixed(1)} × ${s.size_m[2].toFixed(1)} × ${s.size_m[1].toFixed(1)} m`));
  if (s.north_deg !== null && s.north_deg !== undefined)
    dl.append(el('dt', {}, 'North'), el('dd', {}, `read from the compass, rotated ${Math.round(s.rotation_applied_deg)}°`));
  c.append(dl);
}

function drawDownloads() {
  const base = `/api/jobs/${S.job.id}/download/`;
  const items = [
    ['model.glb', 'glTF 2.0 — Blender, Twinmotion'],
    ['model.obj', 'Wavefront geometry'],
    ['model.mtl', 'materials for the OBJ'],
    ['blender_import.py', 'one-command Blender scene'],
    ['model.json', 'every wall, opening and setting'],
    ['model-bundle.zip', 'all of the above'],
  ];
  const ul = $('#downloads'); ul.textContent = '';
  for (const [name, what] of items) {
    ul.append(el('li', {}, el('a', { href: base + name, download: name }, name),
      el('span', { class: 'what' }, what)));
  }
}

// three.js
let renderer, scene3, camera, controls, modelRoot, frameSize = 20;
// A physical sky, used as the background *and*, through PMREM, as the light in
// the scene. Without an environment, glTF PBR materials have nothing to reflect
// and everything reads flat and grey however many lamps you add.
const SUN_ELEVATION = 34;   // degrees above the horizon
const SUN_AZIMUTH = 138;    // degrees, from north

function sunVector(distance = 1) {
  const phi = THREE.MathUtils.degToRad(90 - SUN_ELEVATION);
  const theta = THREE.MathUtils.degToRad(SUN_AZIMUTH);
  return new THREE.Vector3().setFromSphericalCoords(distance, phi, theta);
}

function buildSky(renderer, scene) {
  const sky = new Sky();
  // has to sit inside the camera's far plane or it is clipped and never drawn
  sky.scale.setScalar(2000);
  const u = sky.material.uniforms;
  // Tuned against ACES at this exposure: the Preetham model's usual rayleigh
  // values saturate to white once tone mapped, so the sky reads as haze. These
  // give a daylight blue that still leaves the render bright.
  u.turbidity.value = 3.0;
  u.rayleigh.value = 0.16;
  u.mieCoefficient.value = 0.005;
  u.mieDirectionalG.value = 0.80;
  u.sunPosition.value.copy(sunVector());

  scene.add(sky);

  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();
  const envScene = new THREE.Scene();
  envScene.add(sky.clone());
  scene.environment = pmrem.fromScene(envScene).texture;
  pmrem.dispose();
  return sky;
}

function initViewer() {
  if (renderer) return;
  const host = $('#viewer');
  renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  // 1.0, not the 0.48 this was tuned at. That figure came from a time when
  // every material was near-white and the only risk was blowing out; with a
  // palette that has genuine darks in it, 0.48 crushes them to black.
  renderer.toneMappingExposure = 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.append(renderer.domElement);

  scene3 = new THREE.Scene();
  buildSky(renderer, scene3);

  // Context ground. The exported model carries the plot only; this is the land
  // it sits in, so that looking down at the building shows ground rather than
  // the underside of the sky, which in the sky model is a washed-out neutral.
  const ctx = new THREE.Mesh(
    new THREE.CircleGeometry(260, 64).rotateX(-Math.PI / 2),
    new THREE.MeshStandardMaterial({ color: 0xa8b189, roughness: 1.0 })
  );
  ctx.position.y = -0.18;
  ctx.receiveShadow = true;
  ctx.name = 'Context ground';
  scene3.add(ctx);

  camera = new THREE.PerspectiveCamera(36, 1, 0.1, 6000);
  // Stand it off the target before OrbitControls ever sees it. The render loop
  // below starts immediately and keeps calling controls.update() while the glb
  // is still downloading; with the camera and the target both at the origin the
  // offset between them is zero, the spherical coordinates come out NaN, and
  // that NaN stays in the controls' internal state for good — every later
  // camera.position.set() is undone on the next update and the viewer renders
  // black with nothing in the console to say why.
  camera.position.set(30, 20, 30);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.495;

  const sun = new THREE.DirectionalLight(0xffeacb, 3.0);
  sun.position.copy(sunVector(90));
  sun.castShadow = true;
  sun.shadow.mapSize.set(4096, 4096);
  const d = 45;
  Object.assign(sun.shadow.camera, { left: -d, right: d, top: d, bottom: -d, near: 1, far: 220 });
  sun.shadow.camera.updateProjectionMatrix();
  sun.shadow.bias = -0.00035;
  sun.shadow.normalBias = 0.02;
  scene3.add(sun);
  scene3.add(new THREE.HemisphereLight(0xbcd6f2, 0x7d7565, 0.25));

  // a handle on the scene, for poking at it from the console
  window.f2r = { renderer, scene: scene3, camera, controls, THREE };

  addEventListener('resize', sizeViewer);
  // paint on interaction as well as per frame, so orbiting still works when the
  // browser throttles animation frames in a background tab
  controls.addEventListener('change', () => renderer.render(scene3, camera));
  (function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene3, camera); })();
}

function sizeViewer() {
  if (!renderer) return;
  const host = $('#viewer');
  const w = host.clientWidth || 800, h = host.clientHeight || 480;
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}

async function loadModel() {
  initViewer();
  const url = `/api/jobs/${S.job.id}/download/model.glb?t=${Date.now()}`;
  const gltf = await new GLTFLoader().loadAsync(url);
  if (modelRoot) scene3.remove(modelRoot);
  modelRoot = gltf.scene;
  const maxAniso = renderer.capabilities.getMaxAnisotropy();
  modelRoot.traverse(o => {
    if (!o.isMesh) return;
    o.castShadow = true;
    o.receiveShadow = true;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) {
      if (!m) continue;
      m.side = THREE.DoubleSide;
      m.flatShading = false;
      if (m.map) {
        m.map.anisotropy = maxAniso;
        m.map.wrapS = m.map.wrapT = THREE.RepeatWrapping;
        m.map.needsUpdate = true;
      }
      m.envMapIntensity = 0.62;
    }
  });
  scene3.add(modelRoot);

  const box = new THREE.Box3().setFromObject(modelRoot);
  const size = box.getSize(new THREE.Vector3());
  const centre = box.getCenter(new THREE.Vector3());
  frameSize = Math.max(size.x, size.z, size.y);
  controls.target.copy(centre);
  setView('iso');
  sizeViewer();
  applyVisibility();
}

function setView(which) {
  if (!modelRoot) return;
  const c = controls.target, r = frameSize * 1.15;
  // the top view is deliberately tilted a few degrees: a camera looking exactly
  // down its own up-axis has no defined roll and lands at a random angle
  // north is −Z, so the four elevations are named for the way they face
  const p = {
    iso: [r * 1.15, r * 0.42, r * 1.15],
    south: [0, r * 0.36, r * 1.5],
    north: [0, r * 0.36, -r * 1.5],
    east: [r * 1.5, r * 0.36, 0],
    west: [-r * 1.5, r * 0.36, 0],
    top: [0, r * 1.85, r * 0.3],
  }[which];
  camera.position.set(c.x + p[0], c.y + p[1], c.z + p[2]);
  camera.updateProjectionMatrix();
  controls.update();
  renderer.render(scene3, camera); // paint now: rAF is throttled in a hidden tab
}
$$('[data-view]').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));

function applyVisibility() {
  if (!modelRoot) return;
  const ground = $('#showground').checked, roof = $('#showroof').checked,
        rail = $('#showrail').checked;
  modelRoot.traverse(o => {
    if (!o.name) return;
    if (o.name === 'Ground') o.visible = ground;
    if (o.name === 'Roof slab' || o.name === 'Parapet') o.visible = roof;
    if (o.name.endsWith('railings')) o.visible = rail;
  });
  if (renderer) renderer.render(scene3, camera);
}
['#showground', '#showroof', '#showrail'].forEach(
  id => $(id).addEventListener('change', applyVisibility));

// ─────────────────────────────────────────────────────────── boot
(async function boot() {
  try {
    const jobs = await api('/api/jobs');
    if (jobs.length) {
      $('#recent').hidden = false;
      const ul = $('#recentlist'); ul.textContent = '';
      for (const j of jobs.slice(0, 6)) {
        ul.append(el('li', {},
          el('button', {
            class: 'link', onclick: async () => {
              const r = await api('/api/jobs/' + j.id);
              setJob(r.job); step(Object.keys(r.job.extracts).length ? 'check' : 'sheets');
            }
          }, j.title || j.id),
          ` · ${j.sheets} sheets · ${j.storeys} storeys · ${j.created.slice(0, 16).replace('T', ' ')}`));
      }
    }
  } catch { /* first run */ }
})();
