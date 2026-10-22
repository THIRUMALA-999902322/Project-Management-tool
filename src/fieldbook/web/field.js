/* Fieldbook field app: one person talking to their Assistant.
   Everything sent goes through an offline queue (IndexedDB) with a client id, so a dropped
   signal never loses a photo and a retry never duplicates one. */
(() => {
'use strict';
const TOKEN = location.pathname.split('/').filter(Boolean).pop();
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ic = (name) => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const asst = () => `<span class="asst"><svg aria-hidden="true"><use href="#i-asst"/></svg></span>`;
const fmtTime = (iso) => {
  if (!iso) return '';
  const d = new Date(iso), t = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return d.toDateString() === new Date().toDateString() ? t : `${d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}, ${t}`;
};
const fileUrl = (id) => `/api/field/files/${id}?token=${encodeURIComponent(TOKEN)}`;
const TONE = { ok: 'check', pend: 'clock', warn: 'alert', bad: 'flag', none: 'clock' };
const pill = (tone, text, icon) => `<span class="pill pill--${tone}">${ic(icon || TONE[tone] || 'clock')}${esc(text)}</span>`;
const bar = (a, b) => `<div class="bar" role="img" aria-label="${a}% approved${b > a ? `, ${b}% when approved` : ''}"><span class="bar__ok" style="width:${a}%"></span><span class="bar__pend" style="left:${a}%;width:${b - a}%"></span></div><div class="bar__lbl"><b class="mono">${a}%</b>${b > a ? ` · <span class="mono">${b}%</span> when approved` : ' approved'}</div>`;
function toast(msg, icon = 'check') {
  const el = document.createElement('div'); el.className = 'toast'; el.innerHTML = `${ic(icon)}<span>${esc(msg)}</span>`;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 320); }, 3600);
}
function store(key, val) { try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch { return null; } return null; }

/* ================= API ================= */
async function api(method, path, body) {
  const opts = { method, headers: { Authorization: `Bearer ${TOKEN}` } };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  let data = null; try { data = await res.json(); } catch { data = null; }
  if (!res.ok) { const e = new Error((data && typeof data.detail === 'string' && data.detail) || 'Something went wrong.'); e.status = res.status; throw e; }
  return data;
}

/* ================= offline queue (IndexedDB) ================= */
let idb = null;
function openQueue() {
  return new Promise((resolve) => {
    if (!('indexedDB' in window)) return resolve(null);
    const req = indexedDB.open('fieldbook-' + TOKEN.slice(0, 12), 1);
    req.onupgradeneeded = () => req.result.createObjectStore('items', { keyPath: 'client_id' });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
  });
}
const memQueue = new Map(); // used when IndexedDB is unavailable (private windows)
function qAll() {
  if (!idb) return Promise.resolve([...memQueue.values()]);
  return new Promise((res) => { const r = idb.transaction('items').objectStore('items').getAll(); r.onsuccess = () => res(r.result.sort((a, b) => a.created - b.created)); r.onerror = () => res([]); });
}
function qPut(item) {
  if (!idb) { memQueue.set(item.client_id, item); return Promise.resolve(); }
  return new Promise((res) => { const t = idb.transaction('items', 'readwrite'); t.objectStore('items').put(item); t.oncomplete = res; t.onerror = res; });
}
function qDel(id) {
  if (!idb) { memQueue.delete(id); return Promise.resolve(); }
  return new Promise((res) => { const t = idb.transaction('items', 'readwrite'); t.objectStore('items').delete(id); t.oncomplete = res; t.onerror = res; });
}
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));

/* ================= state ================= */
let ME = null, jobId = null, queued = [], flushing = false, wasOffline = false, geo = null;
const collapsed = new Set();
const first = () => { const n = ME.person.name; return n.split(' ')[0].includes('.') ? n : n.split(' ')[0]; };
const currentJob = () => ME.jobs.find(j => j.id === jobId) || null;
const managerFirst = () => (ME.manager || 'your manager').split(' ')[0];

/* ================= render ================= */
function render() {
  if (!ME) return;
  $('#hello').textContent = `Hi, ${first()}`;
  $('#sub').textContent = `${ME.person.role_label} · your Assistant`;
  document.title = `Fieldbook · ${ME.person.name}`;
  renderJob(); renderDraft(); renderThread(); renderOffline();
}
function renderJob() {
  const j = currentJob();
  const pick = ME.jobs.length > 1 ? `<div class="jobpick" role="group" aria-label="Your jobs">${ME.jobs.map(x => `<button type="button" aria-pressed="${x.id === jobId}" data-job="${x.id}">${esc(x.tag)}</button>`).join('')}</div>` : '';
  $('#jobhead').innerHTML = j
    ? `${pick}<div class="jobcard"><h1>${esc(j.name)}</h1>${bar(j.approved_pct, j.pending_pct)}<div class="job__meta"><span class="mono">${esc(j.qty_label)}</span>${pill(j.status[0], j.status[1])}</div></div>`
    : `<div class="jobcard"><h1>No job yet</h1><p class="explain">${esc(ME.manager)} will add you to one. You can still send photos and notes; I’ll keep them until then.</p></div>`;
}
function openReport() {
  if (!jobId) return null;
  return ME.reports.find(r => r.job_id === jobId && (r.state === 'draft' || r.state === 'changes_requested')) || null;
}
function renderDraft() {
  const r = openReport();
  const last = ME.reports.find(x => x.job_id === jobId && (x.state === 'submitted' || x.state === 'approved'));
  const el = $('#draft');
  if (!r) {
    if (last && last.state === 'submitted') el.innerHTML = `<div class="draft__body">${pill('pend', `Submitted · waiting for ${managerFirst()}`)}<div class="big">${esc(last.change)}${last.where ? `<small>${esc(last.where)}</small>` : ''}</div><p class="quietline">You’ll hear back here. Keep sending; I’ll start tomorrow’s draft.</p></div>`;
    else el.innerHTML = '';
    el.classList.remove('collapsed');
    return;
  }
  const n = r.evidence_count;
  const isChanges = r.state === 'changes_requested';
  el.classList.toggle('collapsed', collapsed.has(r.id));
  el.innerHTML = `<button class="draft__head" data-collapse="${r.id}" aria-expanded="${!collapsed.has(r.id)}"><span>${isChanges ? 'Sent back for changes' : 'Today’s draft'}</span><span>${pill(isChanges ? 'warn' : 'none', isChanges ? 'Changes requested' : 'Draft', isChanges ? 'alert' : 'pen')} ${ic('chevron')}</span></button>
    <div class="draft__body">
      ${isChanges && r.change_note ? `<div class="missing"><b>${esc(managerFirst())} asked for changes</b>${esc(r.change_note)}</div>` : ''}
      <div class="big">${r.delta ? esc(r.change) : '—'}${r.where ? `<small>${esc(r.where)}</small>` : ''}</div>
      ${r.claims.length ? `<ul class="claims">${r.claims.map(c => `<li>${esc(c)}</li>`).join('')}</ul>` : ''}
      ${r.question ? `<div class="note">${asst()} ${esc(r.question)}</div>` : ''}
      ${r.missing.length ? `<div class="missing"><b>Missing</b>${esc(r.missing.join(', '))}</div>` : ''}
      ${r.asked.map(q => `<div class="note"><b>${esc(managerFirst())} asked:</b> ${esc(q)}</div>`).join('')}
      <button class="btn btn--primary btn--block sendbtn" data-submit="${r.id}" ${n ? '' : 'disabled'}>${ic('send')}${isChanges ? 'Send again' : 'Send report'}</button>
      <p class="quietline">${n} item${n === 1 ? '' : 's'} attached · goes to ${esc(ME.manager)} for approval${r.drafted_by === 'claude' ? ' · drafted by Claude' : ''}.</p>
    </div>`;
}
function inputBubble(x, pending) {
  let inner;
  if (x.kind === 'photo') inner = `<img class="photo" alt="Photo${x.text ? ' · ' + esc(x.text) : ''}" src="${pending ? x.url : fileUrl(x.blob_id)}" loading="lazy">`;
  else if (x.kind === 'doc') inner = pending ? `<span class="filechip">${ic('file')}${esc(x.filename)}</span>` : `<a class="filechip" href="${fileUrl(x.blob_id)}" target="_blank" rel="noopener">${ic('file')}${esc(x.text)}</a>`;
  else if (x.kind === 'voice') {
    let w = ''; for (let i = 0; i < 26; i++) w += `<i style="height:${6 + Math.abs(Math.sin(i * 1.7)) * 18}px"></i>`;
    inner = `<div class="mb voice"><span class="wave">${w}</span>${x.quote || x.text_ ? `<span>“${esc(x.quote || x.text_)}”</span>` : '<span>Voice note</span>'}</div>`;
  } else inner = `<div class="mb">${esc(x.text)}</div>`;
  return `<li class="m m--me ${pending ? 'pending' : ''}">${inner}<time>${pending ? 'Waiting to upload' : fmtTime(x.at)}</time></li>`;
}
function assistantBubble(text, at, cls = 'm--a', who = 'Assistant') {
  return `<li class="m ${cls}"><span class="mwho">${cls === 'm--a' ? asst() : ''}${esc(who)}</span><div class="mb">${text}</div><time>${fmtTime(at)}</time></li>`;
}
function renderThread() {
  const reportsHere = new Set(ME.reports.filter(r => r.job_id === jobId).map(r => r.id));
  const items = [];
  ME.inputs.filter(x => !x.report_id || reportsHere.has(x.report_id) || !jobId).forEach(x => items.push({ at: x.at, html: inputBubble(x) }));
  ME.reports.filter(r => reportsHere.has(r.id)).forEach(r => {
    if (r.submitted_at) items.push({ at: r.submitted_at, html: assistantBubble(`Sent to ${esc(ME.manager)}: <b class="mono">${esc(r.change)}</b>${r.where ? ` · ${esc(r.where)}` : ''}`, r.submitted_at) });
    if (r.state === 'approved' && r.lock) items.push({ at: r.lock.at, html: assistantBubble(`${ic('lock')} Approved by ${esc(r.lock.by)} · locked <span class="mono">${esc(r.lock.fingerprint)}</span>`, r.lock.at, 'm--a m--ok') });
    if (r.state === 'changes_requested' && r.change_note) items.push({ at: r.updated_at, html: assistantBubble(esc(r.change_note), r.updated_at, 'm--mgr', `${ME.manager} asked for changes`) });
    r.asked.forEach(q => items.push({ at: r.updated_at, html: assistantBubble(esc(q), r.updated_at, 'm--mgr', ME.manager) }));
  });
  items.sort((a, b) => new Date(a.at) - new Date(b.at));
  const pending = queued.map(q => inputBubble(q, true)).join('');
  $('#thread').innerHTML = items.map(i => i.html).join('') + pending || `<li class="empty-state">Nothing yet. Snap a photo or tell me what you did today.</li>`;
}
function renderOffline() {
  const off = !navigator.onLine;
  const n = queued.length;
  const el = $('#offline');
  el.hidden = !off && !n;
  $('#offlineText').textContent = off ? `Offline · ${n} item${n === 1 ? '' : 's'} queued · they’ll upload automatically` : `Uploading ${n} item${n === 1 ? '' : 's'}…`;
}

/* ================= data ================= */
let refreshTimer = null;
function scheduleRefresh(ms = 300) { clearTimeout(refreshTimer); refreshTimer = setTimeout(load, ms); }
async function load() {
  try { ME = await api('GET', '/api/field/me'); }
  catch (e) {
    if (e.status === 401) { $('#main').innerHTML = `<div class="empty-state"><h1 class="stage__title">This link no longer works</h1><p>Ask your manager for a new one.</p></div>`; $('#composer').hidden = true; }
    return;
  }
  const saved = +store('fb-job-' + TOKEN.slice(0, 12));
  if (!jobId || !ME.jobs.some(j => j.id === jobId)) jobId = ME.jobs.some(j => j.id === saved) ? saved : (ME.jobs[0] || {}).id || null;
  queued = await qAll();
  const firstRender = !$('#thread').children.length;
  render();
  if (firstRender) window.scrollTo({ top: document.body.scrollHeight });
}
async function enqueue(item) {
  item.client_id = uuid(); item.job_id = jobId; item.created = Date.now();
  if (geo && item.kind === 'photo') { item.lat = geo.lat; item.lon = geo.lon; }
  await qPut(item);
  queued = await qAll();
  if (item.file && item.kind === 'photo') item.url = URL.createObjectURL(item.file);
  queued.forEach(q => { if (q.file && q.kind === 'photo' && !q.url) q.url = URL.createObjectURL(q.file); });
  renderThread(); renderOffline();
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  flush();
}
async function flush() {
  if (flushing) return;
  flushing = true;
  try {
    for (const item of await qAll()) {
      if (!navigator.onLine) break;
      const fd = new FormData();
      fd.append('kind', item.kind); fd.append('client_id', item.client_id);
      if (item.text) fd.append('text', item.text);
      if (item.job_id) fd.append('job_id', item.job_id);
      if (item.lat != null) { fd.append('lat', item.lat); fd.append('lon', item.lon); }
      fd.append('captured_at', new Date(item.created).toISOString());
      if (item.file) fd.append('file', item.file, item.filename || 'upload');
      try { await api('POST', '/api/field/inputs', fd); }
      catch (e) {
        if (!e.status) { wasOffline = true; break; } // network: keep it queued
        if (e.status === 401) break;
        toast(e.message, 'alert'); // a bad item must not block the queue
      }
      await qDel(item.client_id);
    }
  } finally { flushing = false; }
  queued = await qAll();
  if (wasOffline && navigator.onLine && !queued.length) {
    wasOffline = false;
    api('POST', '/api/field/heartbeat', { online: true, queued: 0 }).catch(() => {});
  }
  renderOffline();
  scheduleRefresh(500);
}

/* ================= capture ================= */
function addFiles(files, defaultKind) {
  [...files].forEach(f => enqueue({ kind: f.type.startsWith('image/') ? 'photo' : defaultKind, file: f, filename: f.name }));
}
$('#pickPhoto').addEventListener('change', (e) => { addFiles(e.target.files, 'photo'); e.target.value = ''; });
$('#pickFile').addEventListener('change', (e) => { addFiles(e.target.files, 'doc'); e.target.value = ''; });
$('#composer').addEventListener('submit', (e) => {
  e.preventDefault();
  const t = $('#text'); const v = t.value.trim(); if (!v) return;
  enqueue({ kind: 'text', text: v }); t.value = '';
});

let rec = null;
$('#mic').addEventListener('click', async () => {
  const btn = $('#mic');
  if (rec) { rec.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) { toast('Voice notes need microphone access. Type it instead.', 'alert'); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { toast('Microphone is off for this page. Type it instead.', 'alert'); return; }
  const chunks = []; let transcript = '';
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const sr = SR ? new SR() : null;
  if (sr) { sr.continuous = true; sr.interimResults = false; sr.onresult = (ev) => { for (let i = ev.resultIndex; i < ev.results.length; i++) if (ev.results[i].isFinal) transcript += ev.results[i][0].transcript + ' '; }; try { sr.start(); } catch { /* not allowed here */ } }
  rec = new MediaRecorder(stream);
  rec.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
  rec.onstop = () => {
    stream.getTracks().forEach(t => t.stop()); if (sr) try { sr.stop(); } catch { /* already stopped */ }
    btn.classList.remove('rec'); btn.setAttribute('aria-label', 'Record a voice note'); rec = null;
    setTimeout(() => {
      const blob = new Blob(chunks, { type: chunks[0] ? chunks[0].type : 'audio/webm' });
      const text = transcript.trim();
      enqueue({ kind: 'voice', file: blob, filename: 'voice-note.webm', text, text_: text });
      if (!text) toast('Voice note saved. I couldn’t make out the words, so add a line of text too.', 'mic');
    }, 300);
  };
  rec.start(); btn.classList.add('rec'); btn.setAttribute('aria-label', 'Stop recording');
});

/* ================= interactions ================= */
document.addEventListener('click', async (e) => {
  const jb = e.target.closest('[data-job]');
  if (jb) { jobId = +jb.dataset.job; store('fb-job-' + TOKEN.slice(0, 12), String(jobId)); render(); return; }
  const col = e.target.closest('[data-collapse]');
  if (col) { const id = +col.dataset.collapse; collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id); renderDraft(); return; }
  const sub = e.target.closest('[data-submit]');
  if (sub) {
    if (queued.length) { toast('Still uploading. I’ll be ready to send in a moment.', 'clock'); return; }
    sub.disabled = true;
    try { await api('POST', `/api/field/reports/${sub.dataset.submit}/submit`); toast(`Sent to ${ME.manager}. You’ll hear back here.`, 'send'); await load(); }
    catch (err) { toast(err.message, 'alert'); sub.disabled = false; }
  }
});
window.addEventListener('online', () => { renderOffline(); flush(); });
window.addEventListener('offline', () => { wasOffline = true; renderOffline(); });

/* ================= boot ================= */
(async function boot() {
  idb = await openQueue();
  await load();
  if (!ME) return;
  if (navigator.geolocation) navigator.geolocation.getCurrentPosition((p) => { geo = { lat: p.coords.latitude, lon: p.coords.longitude }; }, () => {}, { maximumAge: 600000, timeout: 8000 });
  api('POST', '/api/field/heartbeat', { online: true, queued: queued.length }).catch(() => {});
  flush();
  const es = new EventSource(`/api/field/stream?token=${encodeURIComponent(TOKEN)}`);
  es.onmessage = () => scheduleRefresh(400);
})();
})();
