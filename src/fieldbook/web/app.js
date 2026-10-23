/* Fieldbook office app: the workspace for the owner and project managers.
   One snapshot (/api/office) drives every page; live events (/api/events/stream) keep it current.
   The server is the source of truth and enforces permissions. This file renders and calls the API. */
(() => {
'use strict';

/* ================= helpers ================= */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ic = (name) => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
const isToday = (iso) => new Date(iso).toDateString() === new Date().toDateString();
const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const fmtDay = (iso) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
const fmtWhen = (iso) => iso ? (isToday(iso) ? fmtTime(iso) : `${fmtDay(iso)}, ${fmtTime(iso)}`) : '';
const firstName = (name) => name.split(' ')[0].includes('.') ? name : name.split(' ')[0];
const small = window.matchMedia('(max-width: 900px)');
function ago(iso) {
  if (!iso) return '—';
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return 'Just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (isToday(iso)) return `Today, ${fmtTime(iso)}`;
  if (s < 6 * 86400) return `${new Date(iso).toLocaleDateString([], { weekday: 'short' })}, ${fmtTime(iso)}`;
  return fmtDay(iso);
}
const num = (v) => { const n = parseFloat(String(v ?? '').replace(/[^\d.-]/g, '')); return Number.isFinite(n) ? n : null; };

/* ================= API ================= */
class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
async function api(method, path, body, form) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (form) opts.body = body;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, opts); } catch { throw new ApiError(0, 'Can’t reach the server. Check the connection and try again.'); }
  if (res.status === 401) { showSignIn(); throw new ApiError(401, 'Sign in with your login link.'); }
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const d = data && data.detail;
    throw new ApiError(res.status, typeof d === 'string' ? d : 'Something went wrong. Try again.');
  }
  return data;
}
const fail = (e) => toast(e.message || String(e), 'bad');
function showSignIn() {
  if ($('.signin')) return;
  document.body.innerHTML = `<main class="signin"><section class="card card-body"><h1>Open your login link</h1><p class="muted">Fieldbook signs you in with a private link instead of a password. Open the link you were given, or create one on the server with <code class="mono">fieldbook link owner</code>.</p></section></main>`;
}

/* ================= reference copy (the server enforces permissions) ================= */
const ROLES = {
  owner: { label: 'Owner', can: 'Answers questions about any job from the records.', needs: 'Nothing; the owner approves.' },
  pm: { label: 'Project manager', can: 'Checks each report against the original files and the plan, then drafts a short review with flags and questions.', needs: 'Approving. The Assistant never approves on its own.' },
  engineer: { label: 'Design engineer', can: 'Tracks calcs, design decisions and spec sections, and drafts answers to RFIs.', needs: 'Anything that leaves the company.' },
  cad: { label: 'CAD tech', can: 'Counts sheets drafted against the sheet list and files markups where they belong.', needs: 'Sheet counts, before they count toward the submittal.' },
  qaqc: { label: 'QA/QC reviewer', can: 'Logs review comments by sheet and checks that each one is closed out.', needs: 'Marking a set ready to submit.' },
  crew: { label: 'Crew lead', can: 'Turns photos, voice notes and texts into a daily progress report, and asks which stations when it can’t tell.', needs: 'Every quantity, before it counts toward progress.' },
  inspector: { label: 'Inspector', can: 'Logs inspections, tests and CCTV runs with photos, and flags anything that doesn’t match the plan.', needs: 'Pass or fail results and quantities.' },
  contractor: { label: 'Contractor', can: 'Reports on their own scope only. Can’t see other jobs, people or prices.', needs: 'Every quantity and every change.' },
};
const ROLE_ORDER = ['pm', 'engineer', 'cad', 'qaqc', 'crew', 'inspector', 'contractor'];
const TEAMS = ['Management', 'Design', 'QA/QC', 'Field', 'Contractors'];
const METHOD = { qty: 'Quantity', steps: 'Weighted steps', done: 'Done or not' };
const GROUPS = [['flagged', 'Flagged', 'bad'], ['attention', 'Needs attention', 'warn'], ['routine', 'Routine · evidence complete', 'ok']];
const GROUP_RANK = { flagged: 0, attention: 1, routine: 2 };
const ENTRY_KIND = { report_approved: 'Approved', plan_approved: 'Plan approved' };

/* ================= state ================= */
let S = null;
const UI = {
  panel: null,          // {id, kind: 'ask' | 'changes'} inline panel on an approval
  amend: null,          // ledger entry id being amended
  drawer: null,         // {type: 'person' | 'job', id}
  addJob: null,         // add-job panel in the person drawer
  inputs: {},           // person id -> recent inputs
  projects: {},         // project id -> /api/projects/:id
  plan: null,           // /api/projects/:id/plan while reviewing
  reading: new Map(),   // project id -> {pages, timer} while the Assistant reads documents
  jobs: { q: '', project: 'all', show: 'all' },
  people: { q: '', team: 'All' },
  chat: [],
  fresh: new Set(),
};
const person = (id) => S.people.find(p => p.id === id);
const job = (id) => S.jobs.find(j => j.id === id);
const project = (id) => S.projects.find(p => p.id === id);
const jobsOf = (pid) => S.jobs.filter(j => j.assignee_id === pid);
const isMe = (p) => !!p && p.id === S.me.id;
const waiting = () => S.inbox.filter(i => i.state === 'submitted').sort((a, b) => GROUP_RANK[a.group] - GROUP_RANK[b.group]);
const approverFor = (p) => (p.role === 'pm' ? null : S.people.find(x => x.role === 'pm')) || S.people.find(x => x.role === 'owner');
const nameOf = (id) => (person(id) || { name: 'Former team member' }).name;

/* ================= small components ================= */
const AV = ['#3B5B8C', '#2F7D4F', '#8A5A2B', '#5B4B8A', '#1F6F8B', '#9C3D54', '#4F6B2D', '#6B5B3E', '#0F766E', '#7A4E8C'];
function initials(name) {
  const parts = name.replace(/\./g, ' ').split(/\s+/).filter(Boolean);
  return ((parts[0] || '?')[0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}
function avatar(p, size) {
  const cls = `avatar${size ? ' avatar-' + size : ''}`;
  return p ? `<span class="${cls}" style="--av:${AV[p.id % AV.length]}" aria-hidden="true">${esc(initials(p.name))}</span>` : `<span class="${cls}" aria-hidden="true">?</span>`;
}
const TONE_IC = { ok: 'check', pend: 'clock', warn: 'alert', bad: 'flag', none: 'clock' };
const pill = (tone, text, icon) => `<span class="pill pill-${tone}">${ic(icon || TONE_IC[tone] || 'clock')}${esc(text)}</span>`;
function bar(a, b, compact) {
  const aria = `${a}% approved${b > a ? `, ${b}% once pending reports are approved` : ''}`;
  return `<div class="bar" role="img" aria-label="${aria}"><span class="ok" style="width:${a}%"></span><span class="pend" style="left:${a}%;width:${Math.max(0, b - a)}%"></span></div>`
    + (compact ? '' : `<div class="barlabel"><span><b>${a}%</b> approved</span>${b > a ? `<span>${b}% with pending</span>` : ''}</div>`);
}
const tag = (pr) => pr ? `<span class="tag" style="--c:${pr.color}"><i></i>${esc(pr.short)}</span>` : '';
const ACT_DOT = { ok: 'ok', input: 'pend', work: 'pend', pend: 'pend', warn: 'warn', bad: 'bad' };
const activityInner = (p) => `<span class="dot ${ACT_DOT[p.activity.kind] ? 'dot-' + ACT_DOT[p.activity.kind] : ''}"></span><span>${esc(p.activity.text)}</span>`;
const activity = (p) => `<span class="activity" data-activity="${p.id}">${activityInner(p)}</span>`;
function linkPill(p) {
  if (isMe(p)) return pill('ok', 'Signed in', 'check');
  if (p.link === 'offline') return pill('none', `Offline · ${p.queued} queued`, 'wifi-off');
  if (p.status === 'invited') return pill('pend', 'Invited · not linked yet', 'clock');
  return pill('ok', `Linked · ${p.device}`, 'link');
}
function planPill(pr) {
  if (UI.reading.has(pr.id) || pr.plan_status === 'reading') return pill('pend', 'Reading documents', 'clock');
  return { review: pill('warn', 'Plan to review', 'clock'), approved: pill('ok', 'Plan approved', 'lock') }[pr.plan_status] || pill('none', 'No plan yet', 'clock');
}
function evidence(c) {
  if (!c) return '';
  const k = c.kind === 'photo' ? 'photo' : c.kind === 'voice' ? 'voice' : 'doc';
  const thumb = k === 'photo' && c.blob_id ? `<img src="/api/files/${c.blob_id}" alt="" loading="lazy">` : ic(k === 'voice' ? 'mic' : k === 'photo' ? 'image' : 'file');
  return `<button class="evidence" data-act="evidence" data-blob="${c.blob_id || ''}" data-kind="${k}" data-label="${esc(c.label)}"><span class="thumb">${thumb}</span><span>${esc(c.label)}</span></button>`;
}
function row({ href, act, id, tone = 'none', icon, t, s, tile }) {
  const inner = `${tile || `<span class="tile tile-${tone}">${ic(icon)}</span>`}<span class="grow"><span class="t">${esc(t)}</span>${s ? `<span class="s">${esc(s)}</span>` : ''}</span>${ic('chevron-right')}`;
  return `<li>${act ? `<button class="row" data-act="${act}"${id != null ? ` data-id="${id}"` : ''}>${inner}</button>` : `<a class="row" href="${href}">${inner}</a>`}</li>`;
}
const bullets = (xs) => xs && xs.length ? `<ul class="bullets">${xs.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="muted">—</p>';
const projTile = (pr) => `<span class="tile" style="background:color-mix(in srgb, ${pr.color} 14%, transparent); color:${pr.color}">${ic('folder')}</span>`;
function crumbs(list) {
  $('#crumbs').innerHTML = list.map((c, k) => k === list.length - 1 ? `<b>${esc(c[0])}</b>` : `<a href="${c[1]}">${esc(c[0])}</a>${ic('chevron-right')}`).join('');
  document.title = `${list[list.length - 1][0]} · Fieldbook`;
}

/* ================= toasts, modal, drawer ================= */
function toast(msg, tone = 'ok', opts = {}) {
  const icon = opts.icon || { ok: 'check-circle', warn: 'alert', bad: 'flag', pend: 'inbox' }[tone] || 'info';
  const el = document.createElement('div');
  el.className = `toast ${tone}`;
  el.innerHTML = `${ic(icon)}<div>${esc(msg)}${opts.href ? ` <a class="linkbtn" href="${opts.href}">${esc(opts.label || 'Open')}</a>` : ''}</div>`;
  $('#toasts').appendChild(el);
  const kill = () => { el.classList.add('out'); setTimeout(() => el.remove(), 260); };
  el.addEventListener('click', kill);
  setTimeout(kill, opts.href ? 7000 : 4200);
}
let lastFocus = null;
function modal({ title, sub = '', body, foot = '', wide = false }) {
  if (!$('#modalRoot').innerHTML) lastFocus = document.activeElement;
  $('#modalRoot').innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="mTitle"><div class="scrim" data-act="close-modal"></div>
    <div class="modal-card ${wide ? 'wide' : ''}"><div class="modal-head"><div class="grow"><h2 id="mTitle">${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ''}</div><button class="iconbtn" data-act="close-modal" aria-label="Close">${ic('x')}</button></div>
    <div class="modal-body">${body}</div>${foot ? `<div class="modal-foot">${foot}</div>` : ''}</div></div>`;
  const f = $('#modalRoot .modal-body input:not([type=radio]):not([type=checkbox]):not(.vh), #modalRoot .modal-body textarea') || $('#modalRoot .modal-foot .btn:last-child');
  if (f) f.focus();
}
function closeModal() {
  $('#modalRoot').innerHTML = '';
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  lastFocus = null;
}
function confirmBox({ title, sub, body, ok, okClass = 'btn-primary', act, data = {} }) {
  const attrs = Object.entries(data).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('');
  modal({ title, sub, body, foot: `<button class="btn btn-secondary" data-act="close-modal">Cancel</button><button class="btn ${okClass}" data-act="${act}"${attrs}>${ok}</button>` });
}
function openDrawer(type, id) {
  UI.drawer = { type, id };
  UI.addJob = null;
  renderDrawer(true, true);
  if (type === 'person') loadInputs(id);
}
function closeDrawer() { UI.drawer = null; UI.addJob = null; $('#drawerRoot').innerHTML = ''; }
function renderDrawer(force, fresh) {
  const root = $('#drawerRoot');
  if (!UI.drawer) { root.innerHTML = ''; return; }
  if (!force && editing(root)) return;
  const html = UI.drawer.type === 'person' ? personDrawer(UI.drawer.id) : jobDrawer(UI.drawer.id);
  if (html == null) { closeDrawer(); return; }
  const old = $('.drawer-body', root);
  const y = old ? old.scrollTop : 0;
  root.innerHTML = `<div class="scrim" data-act="close-drawer"></div><aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="dTitle">${html}</aside>`;
  if (old && !fresh) { $('.drawer', root).style.animation = 'none'; $('.scrim', root).style.animation = 'none'; $('.drawer-body', root).scrollTop = y; }
  if (fresh) $('.drawer .iconbtn', root).focus();
}
function editing(root) {
  const a = document.activeElement;
  return !!((a && root.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) || root.querySelector('[data-keep]'));
}

/* ================= router ================= */
function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  return { page: parts[0] || 'overview', id: parts[1] ? +parts[1] : null, sub: parts[2] || null };
}
let lastHash = null;
function render(nav) {
  if (!S) return;
  const r = route();
  renderChrome(r);
  const view = $('#view');
  if (nav || (!editing(view) && !STATIC_PAGES.has(r.page))) {
    const same = location.hash === lastHash;
    const y = scrollY;
    (PAGES[r.page] || PAGES.overview)(r, view);
    if (same) scrollTo(0, y); else { scrollTo(0, 0); view.focus({ preventScroll: true }); }
    lastHash = location.hash;
  }
  renderDrawer(false);
}
function renderChrome(r) {
  $('#orgName').textContent = S.org.name;
  const n = S.stats.waiting, badge = $('#navInbox');
  badge.hidden = !n; badge.textContent = n;
  $$('.nav a').forEach(a => { if (a.dataset.nav === r.page) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  const llm = S.assistant.mode === 'llm';
  $('#engine').innerHTML = llm
    ? `<span class="dot dot-ok"></span><span><b>Local model</b><span>${esc(S.assistant.label.split(' · ').slice(1).join(' · '))}</span></span>`
    : `<span class="dot"></span><span><b>Rules engine</b><span>No model set. Point FIELDBOOK_LLM_MODEL at a local model to read photos and PDFs.</span></span>`;
  const me = person(S.me.id) || { id: S.me.id, name: S.me.name, role_label: ROLES[S.me.role].label };
  $('#meBlock').innerHTML = `${avatar(me, 'sm')}<span><b>${esc(me.name)}</b><span>${esc(ROLES[S.me.role].label)}</span></span>`;
}

/* ================= overview ================= */
function feedHTML() {
  // Newest first by time; seeded or backfilled events don't always arrive in time order.
  const items = S.log.slice().sort((x, y) => y.at.localeCompare(x.at) || y.id - x.id).slice(0, 12);
  if (!items.length) return '<li class="empty">Nothing yet. Activity shows up here as people send updates.</li>';
  return items.map(e => `<li class="${UI.fresh.has(e.id) ? 'fresh' : ''}"><time>${esc(isToday(e.at) ? fmtTime(e.at) : fmtDay(e.at))}</time><div>${esc(e.text)}${e.chip ? `<div>${evidence(e.chip)}</div>` : ''}</div></li>`).join('');
}
function renderOverview(r, view) {
  crumbs([['Overview']]);
  const st = S.stats, w = waiting(), me = person(S.me.id);
  const h = new Date().getHours();
  const greet = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  const plans = S.projects.filter(p => p.plan_status === 'review');
  const unassigned = S.jobs.filter(j => !j.assignee_id);
  const offline = S.people.filter(p => !isMe(p) && p.link === 'offline');
  const routine = w.filter(i => i.group === 'routine');
  const needs = [];
  w.filter(i => i.group !== 'routine').forEach(i => {
    const j = job(i.job_id) || {};
    needs.push(row({ href: `#/inbox/${i.id}`, tone: i.group === 'flagged' ? 'bad' : 'warn', icon: i.group === 'flagged' ? 'flag' : 'alert', t: `${nameOf(i.person_id)} · ${j.tag || ''} · ${i.change}`, s: i.verdict }));
  });
  if (routine.length) needs.push(row({ href: '#/inbox', tone: 'ok', icon: 'check-circle', t: `${plural(routine.length, 'routine report')} ready to approve`, s: 'Evidence is complete. You can approve them together.' }));
  plans.forEach(p => needs.push(row({ href: `#/projects/${p.id}/plan`, tone: 'pend', icon: 'file', t: `Plan for ${p.name} is ready for review`, s: 'Check the quantities and who reports each item, then approve.' })));
  if (unassigned.length) needs.push(row({ act: 'jobs-unassigned', tone: 'warn', icon: 'briefcase', t: `${plural(unassigned.length, 'job')} with nobody assigned`, s: unassigned.slice(0, 3).map(j => j.name).join(' · ') }));
  offline.forEach(p => needs.push(row({ act: 'open-person', id: p.id, tone: 'none', icon: 'wifi-off', t: `${p.name} is offline`, s: `${plural(p.queued, 'item')} queued on their ${p.device}. They upload when the signal is back.` })));

  view.innerHTML = `
    <div class="page-head"><div><h1>${greet}, ${esc(firstName(me ? me.name : S.me.name))}</h1><p>${esc(S.org.name)} · ${new Date().toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}</p></div></div>
    <div class="kpis">
      <a class="card kpi" href="#/inbox"><span class="label">${ic('inbox')}Waiting for approval</span><span class="value">${st.waiting}</span><span class="sub ${st.flagged ? 'bad' : ''}">${st.flagged ? `${st.flagged} flagged by the Assistant` : st.waiting ? 'Each checked against the original files' : 'Nothing waiting'}</span></a>
      <div class="card kpi"><span class="label">${ic('chart')}Overall progress</span><span class="value">${st.approved_pct}<small>%</small></span><span class="sub">${st.pending_pct > st.approved_pct ? `${st.pending_pct}% once pending reports are approved` : 'Approved work only'}</span></div>
      <a class="card kpi" href="#/people"><span class="label">${ic('activity')}Active today</span><span class="value">${st.active_today}<small> / ${st.people}</small></span><span class="sub">People who sent an update today</span></a>
      <a class="card kpi" href="#/projects"><span class="label">${ic('folder')}Projects</span><span class="value">${S.projects.length}</span><span class="sub ${plans.length ? 'warn' : ''}">${plans.length ? `${plural(plans.length, 'plan')} waiting for approval` : `${plural(S.jobs.length, 'job')} tracked`}</span></a>
    </div>
    <div class="grid-main">
      <div class="stack">
        <section class="card"><div class="card-head"><h2>Needs your attention</h2><span class="muted">${needs.length ? plural(needs.length, 'item') : ''}</span></div>
          <ul class="list">${needs.join('') || `<li class="empty">${ic('check-circle')}You’re all caught up.</li>`}</ul></section>
        <section class="card"><div class="card-head"><h2>Projects</h2><a class="linkbtn" href="#/projects">View all</a></div>
          <ul class="list">${S.projects.map(p => `<li><a class="row" href="#/projects/${p.id}">${projTile(p)}<span class="grow"><span class="t">${esc(p.name)}</span><span class="s">${esc(p.client || 'No client')} · ${plural(S.jobs.filter(j => j.project_id === p.id).length, 'job')}</span></span><span class="hide-sm" style="width:220px">${bar(p.pct[0], p.pct[1])}</span>${ic('chevron-right')}</a></li>`).join('') || '<li class="empty">No projects yet.</li>'}</ul></section>
      </div>
      <section class="card"><div class="card-head"><h2>Recent activity</h2><span class="muted">Live</span></div><ul class="list feed" id="feed">${feedHTML()}</ul></section>
    </div>`;
}

/* ================= approvals ================= */
function inboxItem(i, sel) {
  const p = person(i.person_id), j = job(i.job_id) || {};
  const s = i.state === 'submitted' ? i.verdict : i.state === 'approved' ? `Locked ${i.lock ? i.lock.fingerprint : ''}` : (i.change_note || 'Sent back');
  return `<a class="ri" href="#/inbox/${i.id}" aria-current="${!!sel && sel.id === i.id}">${avatar(p, 'sm')}<span class="grow"><span class="top"><b>${esc(p ? p.name : 'Former team member')}</b><span class="chg">${esc(i.change)}</span></span><span class="s">${esc(j.tag || '')} · ${esc(s || '')}</span></span></a>`;
}
const ghead = (label, tone, n) => `<div class="ghead"><span class="dot dot-${tone}"></span>${esc(label)}<span style="margin-left:auto">${n}</span></div>`;
function renderInbox(r, view) {
  const w = waiting();
  let sel = r.id ? S.inbox.find(i => i.id === r.id) : null;
  if (!r.id && !small.matches) sel = w[0] || null;
  crumbs(sel && r.id ? [['Approvals', '#/inbox'], [`${nameOf(sel.person_id)} · ${(job(sel.job_id) || {}).tag || 'Report'}`]] : [['Approvals']]);
  const routine = w.filter(i => i.group === 'routine');
  let list = '';
  GROUPS.forEach(([g, label, tone]) => {
    const items = w.filter(i => i.group === g);
    if (items.length) list += ghead(label, tone, items.length) + items.map(i => inboxItem(i, sel)).join('');
  });
  const changes = S.inbox.filter(i => i.state === 'changes_requested');
  if (changes.length) list += ghead('Sent back for changes', 'warn', changes.length) + changes.map(i => inboxItem(i, sel)).join('');
  const done = S.inbox.filter(i => i.state === 'approved');
  if (done.length) list += ghead('Approved · last 24 hours', 'ok', done.length) + done.map(i => inboxItem(i, sel)).join('');
  if (!list) list = `<p class="empty">${ic('check-circle')}Nothing waiting. Reports land here when people send them.</p>`;
  let detail = '<p class="empty">Select a report to review it.</p>';
  if (sel) detail = reportDetail(sel);
  else if (r.id) detail = `<div class="section"><a class="btn btn-ghost btn-sm back" href="#/inbox">${ic('chevron-left')}All approvals</a></div><p class="empty">This report has been dealt with and is no longer waiting. Its approval is in the project history.</p>`;
  view.innerHTML = `
    <div class="page-head"><div><h1>Approvals</h1><p>${w.length ? `${plural(w.length, 'report')} waiting. The project manager’s Assistant checked each one against the original photos and files.` : 'You’re all caught up.'}</p></div>
      <div class="actions">${routine.length > 1 ? `<button class="btn btn-secondary" data-act="approve-routine">${ic('check')}Approve ${routine.length} routine reports</button>` : ''}</div></div>
    <div class="split ${r.id ? 'has-detail' : ''}">
      <section class="card split-list" aria-label="Reports">${list}</section>
      <section class="card split-detail" aria-label="Report">${detail}</section>
    </div>`;
}
function reportDetail(i) {
  const p = person(i.person_id), j = job(i.job_id) || {}, pr = project(j.project_id);
  const tone = i.group === 'flagged' ? 'bad' : i.group === 'attention' ? 'warn' : 'ok';
  const state = i.state === 'approved' ? pill('ok', 'Approved · locked', 'lock')
    : i.state === 'changes_requested' ? pill('warn', 'Sent back for changes', 'alert')
      : pill(tone, (GROUPS.find(g => g[0] === i.group) || [0, 'Waiting'])[1].split(' · ')[0]);
  const by = (x) => x === 'llm' ? 'the local model' : 'the rules engine';
  const checks = [
    ...i.flags.map(f => `<li class="${esc(f.tone)}">${ic(f.tone === 'bad' ? 'flag' : 'alert')}<span>${esc(f.text)}</span></li>`),
    i.usual ? `<li>${ic('clock')}<span>${esc(i.usual)}</span></li>` : '',
    i.missing.length ? `<li class="warn">${ic('alert')}<span><b>Missing:</b> ${esc(i.missing.join(', '))}</span></li>` : '',
  ].join('');
  const panel = UI.panel && UI.panel.id === i.id ? (UI.panel.kind === 'ask' ? askPanel(i, p) : changesPanel(i)) : '';
  return `
    <div class="section back"><a class="btn btn-ghost btn-sm" href="#/inbox">${ic('chevron-left')}All approvals</a></div>
    <div class="detail-head">${avatar(p, 'lg')}
      <div class="grow"><h2>${esc(p ? p.name : 'Former team member')}</h2>
        <div class="s"><span>${esc(j.name || '')}</span>${tag(pr)}${state}</div>
        <div class="s">${esc(p ? p.role_label : '')}${i.submitted_at ? ` · sent ${esc(fmtWhen(i.submitted_at))}` : ''}</div></div>
      <div class="qty"><b>${esc(i.change)}</b>${i.where ? `<span>${esc(i.where)}</span>` : ''}</div>
    </div>
    ${i.verdict ? `<div class="section"><div class="verdict ${tone}">${ic('spark')}<div><b>Assistant’s review</b>${esc(i.verdict)}</div></div></div>` : ''}
    ${i.state === 'changes_requested' && i.change_note ? `<div class="section"><div class="callout warn">${ic('alert')}<div><b>Sent back</b>${esc(i.change_note)}</div></div></div>` : ''}
    ${i.lock ? `<div class="section"><div class="callout ok">${ic('lock')}<div><b>Approved by ${esc(i.lock.by)} · ${esc(fmtWhen(i.lock.at))}</b>Locked in the record as <span class="mono">${esc(i.lock.fingerprint)}</span>. Corrections go in as amendments; nothing is erased.</div></div></div>` : ''}
    <div class="section"><h3>Evidence · ${plural(i.evidence_count, 'item')}</h3><div class="chips">${i.chips.map(evidence).join('') || '<span class="muted">Text only, no files attached.</span>'}</div></div>
    <div class="section cols"><div><h4>Claimed</h4>${bullets(i.claimed)}</div><div><h4>The evidence shows</h4>${bullets(i.shows)}</div></div>
    ${checks ? `<div class="section"><h3>Checks</h3><ul class="flags">${checks}</ul></div>` : ''}
    ${i.asked.length ? `<div class="section"><h3>Your questions</h3><ul class="flags">${i.asked.map(q => `<li>${ic('chat')}<span>${esc(q)}</span></li>`).join('')}</ul></div>` : ''}
    ${panel ? `<div class="section" data-keep>${panel}</div>` : ''}
    <div class="section small muted">${esc(i.note || 'Checked against the original files, not the summary.')} Drafted by ${by(i.drafted_by)}${i.checked_by ? `; evidence read by ${by(i.checked_by)}` : ''}.</div>
    ${i.state === 'submitted' && !panel ? `<div class="detail-actions"><button class="btn btn-secondary" data-act="req-changes" data-id="${i.id}">Request changes</button><button class="btn btn-secondary" data-act="ask-item" data-id="${i.id}">${ic('chat')}Ask ${esc(p ? firstName(p.name) : 'a question')}</button><button class="btn btn-approve" data-act="approve" data-id="${i.id}">${ic('check')}Approve</button></div>` : ''}`;
}
function askPanel(i, p) {
  return `<div class="panel"><h4>${ic('chat')}Ask ${esc(p ? p.name : '')}</h4>
    ${i.questions.length ? `<div class="chips">${i.questions.map(q => `<button class="chipbtn" data-act="send-q" data-id="${i.id}" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>` : ''}
    <label class="field"><span>${i.questions.length ? 'Or write your own' : 'Question'}</span><input id="qText" placeholder="Type a question" autocomplete="off"></label>
    <div class="panel-acts"><button class="btn btn-secondary btn-sm" data-act="panel-cancel">Cancel</button><button class="btn btn-primary btn-sm" data-act="send-q" data-id="${i.id}">${ic('send')}Send question</button></div></div>`;
}
function changesPanel(i) {
  const list = i.missing.length ? i.missing : i.flags.map(f => f.text).slice(0, 3);
  return `<div class="panel"><h4>${ic('alert')}Request changes</h4>
    ${list.length ? `<div class="checks">${list.map(m => `<label class="check"><input type="checkbox" checked value="${esc(m)}"><span>${esc(m)}</span></label>`).join('')}</div>` : ''}
    <label class="field"><span>Note to the sender</span><textarea id="cNote" placeholder="What should they fix or add?"></textarea></label>
    <div class="panel-acts"><button class="btn btn-secondary btn-sm" data-act="panel-cancel">Cancel</button><button class="btn btn-primary btn-sm" data-act="send-changes" data-id="${i.id}">${ic('send')}Send back</button></div></div>`;
}
function confirmApprove(ids) {
  const items = ids.map(id => S.inbox.find(x => x.id === id)).filter(Boolean);
  const flagged = items.some(i => i.group === 'flagged');
  confirmBox({
    title: items.length > 1 ? `Approve ${items.length} reports?` : 'Approve this report?',
    sub: 'Approved work counts toward progress and is locked in the record.',
    body: `<ul class="files">${items.map(i => `<li><span class="grow">${esc(nameOf(i.person_id))} · ${esc((job(i.job_id) || {}).tag || '')}</span><span class="mono">${esc(i.change)}</span></li>`).join('')}</ul>
      ${flagged ? `<div class="callout bad">${ic('flag')}<div>This report is flagged. The flag stays on the record next to your approval.</div></div>` : ''}
      <p class="muted">Mistakes can be corrected later with an amendment. Nothing is ever erased.</p>`,
    ok: `${ic('lock')}Approve and lock`, okClass: 'btn-approve', act: 'do-approve', data: { ids: ids.join(',') },
  });
}
async function doApprove(ids, btn) {
  btn.disabled = true;
  let res;
  try { res = ids.length > 1 ? (await api('POST', '/api/reports/approve', { ids })).approved : [await api('POST', `/api/reports/${ids[0]}/approve`)]; }
  catch (e) { btn.disabled = false; fail(e); return; }
  closeModal();
  const next = waiting().find(i => !ids.includes(i.id));
  await refresh();
  toast(ids.length > 1 ? `${ids.length} reports approved and locked. Progress updated.` : `Approved and locked as ${res[0].fingerprint}. Progress updated.`, 'ok', { icon: 'lock' });
  if (route().page === 'inbox') location.hash = next ? `#/inbox/${next.id}` : '#/inbox';
}

/* ================= projects ================= */
function renderProjects(r, view) {
  if (r.id) return renderProject(r, view);
  crumbs([['Projects']]);
  view.innerHTML = `
    <div class="page-head"><div><h1>Projects</h1><p>${plural(S.projects.length, 'project')} · ${plural(S.jobs.length, 'job')} tracked</p></div>
      <div class="actions"><button class="btn btn-primary" data-act="new-project">${ic('plus')}New project</button></div></div>
    <section class="card"><div class="table-wrap"><table class="tbl">
      <thead><tr><th>Project</th><th class="hide-sm">Plan</th><th class="hide-sm r">Jobs</th><th class="hide-sm">Team</th><th>Progress</th></tr></thead>
      <tbody>${S.projects.map(p => {
        const team = p.team.map(person).filter(Boolean);
        return `<tr class="click" tabindex="0" data-href="#/projects/${p.id}"><td><div class="who">${projTile(p)}<div><b>${esc(p.name)}</b><span>${esc(p.client || 'No client')}${p.work_types.length ? ' · ' + esc(p.work_types.join(', ')) : ''}</span></div></div></td>
          <td class="hide-sm">${planPill(p)}</td><td class="hide-sm r mono">${S.jobs.filter(j => j.project_id === p.id).length}</td>
          <td class="hide-sm"><div class="avatars">${team.slice(0, 5).map(x => avatar(x, 'sm')).join('')}${team.length > 5 ? `<span class="avatar avatar-sm" style="--av:var(--none)">+${team.length - 5}</span>` : ''}</div></td>
          <td class="w-bar">${bar(p.pct[0], p.pct[1])}</td></tr>`;
      }).join('') || `<tr><td colspan="5" class="empty">No projects yet. Create one from its bid form or plan set.</td></tr>`}</tbody></table></div></section>`;
}
function renderProject(r, view) {
  const pr = project(r.id);
  if (!pr) { crumbs([['Projects', '#/projects'], ['Not found']]); view.innerHTML = '<p class="empty">This project doesn’t exist or was removed.</p>'; return; }
  if (r.sub === 'plan') { renderPlan(pr, view); return; }
  crumbs([['Projects', '#/projects'], [pr.name]]);
  view.innerHTML = projectHTML(pr, UI.projects[pr.id]);
  loadProject(pr.id);
}
async function loadProject(id) {
  const at = location.hash;
  try {
    UI.projects[id] = await api('GET', `/api/projects/${id}`);
    const view = $('#view');
    if (location.hash === at && !editing(view)) { const y = scrollY; view.innerHTML = projectHTML(project(id), UI.projects[id]); scrollTo(0, y); }
  } catch (e) { if (e.status !== 401) fail(e); }
}
function jobRow(j, showProject) {
  const pr = project(j.project_id), w = j.assignee_id && person(j.assignee_id);
  return `<tr class="click" tabindex="0" data-job="${j.id}">
    <td><b>${esc(j.name)}</b>${showProject ? `<div style="margin-top:4px">${tag(pr)}</div>` : ''}</td>
    <td>${w ? `<div class="who">${avatar(w, 'sm')}<div><b>${esc(w.name)}</b><span>${esc(w.role_label)}</span></div></div>` : '<span class="nobody">Unassigned</span>'}</td>
    <td class="w-bar">${bar(j.approved_pct, j.pending_pct)}</td>
    <td class="hide-sm mono">${esc(j.qty_label)}</td>
    <td>${pill(j.status[0], j.status[1])}</td>
    <td class="hide-sm muted">${esc(ago(j.last_update))}</td></tr>`;
}
const jobsHead = '<thead><tr><th>Job</th><th>Assigned to</th><th>Progress</th><th class="hide-sm">Quantity</th><th>Status</th><th class="hide-sm">Last update</th></tr></thead>';
function projectHTML(pr, full) {
  const jobs = S.jobs.filter(j => j.project_id === pr.id);
  const team = pr.team.map(person).filter(Boolean);
  const reading = UI.reading.has(pr.id) || pr.plan_status === 'reading';
  const pending = waiting().filter(i => jobs.some(j => j.id === i.job_id));
  const rd = UI.reading.get(pr.id);
  let banner = '';
  if (reading) banner = `<div class="callout">${'<span class="spinner" aria-hidden="true"></span>'}<div><b>The Assistant is reading the scope documents${rd && rd.pages ? ` (${plural(rd.pages, 'page')})` : ''}</b>It proposes jobs and quantities from them. This page moves to the plan review when it’s done; you can keep working meanwhile.</div></div>`;
  else if (pr.plan_status === 'review') banner = `<div class="callout warn">${ic('clock')}<div><b>The proposed plan is waiting for your approval</b>Nothing counts toward progress until the plan is approved. <a href="#/projects/${pr.id}/plan">Review the plan</a></div></div>`;
  else if (pr.plan_status === 'draft') banner = `<div class="callout">${ic('info')}<div><b>No plan yet</b>Let the Assistant propose jobs and quantities from the uploaded documents, or add jobs one at a time.</div></div>`;
  const history = full ? (full.history.map(h => entryHTML(h, pr.id)).join('') || '<p class="empty">Nothing approved yet. Approved reports and plans are locked here.</p>') : '<p class="loading">Loading…</p>';
  return `
    <div class="page-head"><div><h1>${esc(pr.name)}</h1><p>${esc(pr.client || 'No client')}${pr.work_types.length ? ' · ' + esc(pr.work_types.join(', ')) : ''}</p><div class="pills">${planPill(pr)}</div></div>
      <div class="actions">
        ${pr.plan_status === 'draft' && !reading ? `<button class="btn btn-secondary" data-act="propose-plan" data-proj="${pr.id}">${ic('spark')}Propose a plan</button>` : ''}
        <button class="btn btn-secondary" data-act="new-job" data-proj="${pr.id}">${ic('plus')}Add job</button>
        ${pr.plan_status === 'review' && !reading ? `<a class="btn btn-primary" href="#/projects/${pr.id}/plan">Review plan</a>` : ''}
      </div></div>
    ${banner ? `<div style="margin-bottom:20px">${banner}</div>` : ''}
    <div class="kpis">
      <div class="card kpi"><span class="label">${ic('chart')}Progress</span><span class="value">${pr.pct[0]}<small>%</small></span><span class="sub">${pr.pct[1] > pr.pct[0] ? `${pr.pct[1]}% once pending reports are approved` : 'Approved work only'}</span></div>
      <div class="card kpi"><span class="label">${ic('list')}Jobs</span><span class="value">${jobs.length}</span><span class="sub ${jobs.some(j => !j.assignee_id) ? 'warn' : ''}">${jobs.some(j => !j.assignee_id) ? `${jobs.filter(j => !j.assignee_id).length} unassigned` : 'All assigned'}</span></div>
      <a class="card kpi" href="#/inbox"><span class="label">${ic('inbox')}Waiting for approval</span><span class="value">${pending.length}</span><span class="sub">Reports on this project</span></a>
      <div class="card kpi"><span class="label">${ic('users')}Team</span><span class="value">${team.length}</span><span class="sub">People with access to this project</span></div>
    </div>
    <section class="card" style="margin-bottom:20px"><div class="card-head"><h2>Jobs</h2><span class="muted">${plural(jobs.length, 'job')}</span></div>
      ${jobs.length ? `<div class="table-wrap"><table class="tbl">${jobsHead}<tbody>${jobs.map(j => jobRow(j, false)).join('')}</tbody></table></div>` : '<p class="empty">Jobs appear here once the plan is approved.</p>'}</section>
    <div class="grid-main">
      <section class="card"><div class="card-head"><h2>History</h2><span class="muted">Locked entries · newest first</span></div>${history}</section>
      <section class="card"><div class="card-head"><h2>Team</h2><span class="muted">${plural(team.length, 'person', 'people')}</span></div>
        <ul class="list">${team.map(p => `<li><button class="row" data-act="open-person" data-id="${p.id}">${avatar(p)}<span class="grow"><span class="t">${esc(p.name)}</span><span class="s">${esc(p.role_label)}${p.company ? ' · ' + esc(p.company) : ''}</span></span>${ic('chevron-right')}</button></li>`).join('') || '<li class="empty">Nobody yet.</li>'}</ul></section>
    </div>`;
}
function entryHTML(h, prid) {
  return `<div class="entry"><div class="entry-top">${ic('lock')}<span>${esc(ENTRY_KIND[h.kind] || 'Recorded')} by ${esc(h.by)} · ${esc(fmtWhen(h.at))}</span><span class="hash" title="SHA-256 ${esc(h.hash)}">${esc(h.fingerprint)}</span></div>
    <div class="entry-text ${h.amendment ? 'struck' : ''}">${esc(h.text)}</div>
    ${h.amendment ? `<div class="amend"><div class="entry-top">${ic('pen')}<span>Amended by ${esc(h.amendment.by)} · ${esc(fmtWhen(h.amendment.at))}</span><span class="hash">${esc(h.amendment.fingerprint)}</span></div><div class="entry-text">${esc(h.amendment.text)}</div><div class="small muted">Reason: ${esc(h.amendment.reason)}</div></div>` : ''}
    ${UI.amend === h.id ? amendForm(h, prid) : `<button class="linkbtn" data-act="amend" data-entry="${h.id}">Amend</button>`}</div>`;
}
function amendForm(h, prid) {
  const cur = h.amendment ? h.amendment.text : h.text;
  return `<div class="panel" data-keep><h4>${ic('pen')}Amend this entry</h4>
    <label class="field"><span>Corrected entry</span><input id="amT" value="${esc(cur)}"></label>
    ${h.delta != null ? `<label class="field"><span>Corrected quantity</span><input id="amD" class="mono" inputmode="decimal" value="${esc(h.delta)}"></label>` : ''}
    <label class="field"><span>Reason</span><textarea id="amW" placeholder="What was wrong, and how you know"></textarea><span class="err" id="amErr" hidden>Add a reason. Amendments always say why.</span></label>
    <div class="panel-acts"><button class="btn btn-secondary btn-sm" data-act="amend-cancel">Cancel</button><button class="btn btn-primary btn-sm" data-act="amend-preview" data-proj="${prid}" data-entry="${h.id}">Review amendment</button></div></div>`;
}

/* ----- plan review ----- */
async function renderPlan(pr, view) {
  crumbs([['Projects', '#/projects'], [pr.name, `#/projects/${pr.id}`], ['Plan review']]);
  const at = location.hash;
  if (!UI.plan || UI.plan.project.id !== pr.id) view.innerHTML = '<p class="loading">Loading the plan…</p>';
  try { UI.plan = await api('GET', `/api/projects/${pr.id}/plan`); } catch (e) { fail(e); return; }
  if (location.hash !== at) return;
  const plan = UI.plan, st = plan.project.plan_status;
  if (st !== 'review') {
    view.innerHTML = `<div class="page-head"><div><h1>Plan review</h1><p>${esc(pr.name)}</p></div></div>
      <div class="callout ${st === 'approved' ? 'ok' : ''}">${ic(st === 'approved' ? 'lock' : 'info')}<div><b>${st === 'approved' ? 'This plan is already approved and locked.' : st === 'reading' ? 'The Assistant is still reading the documents.' : 'There’s no proposed plan yet.'}</b><a href="#/projects/${pr.id}">Back to the project</a></div></div>`;
    return;
  }
  const missing = plan.items.filter(it => it.method === 'qty' && it.quantity == null).length;
  view.innerHTML = `<div data-keep>
    <div class="page-head"><div><h1>Review the plan</h1><p>${esc(pr.name)} · the Assistant read the scope documents and proposes ${plural(plan.items.length, 'item')}.</p></div>
      <div class="actions"><a class="btn btn-secondary" href="#/projects/${pr.id}">Not now</a><button class="btn btn-approve" data-act="plan-approve" data-proj="${pr.id}">${ic('lock')}Approve plan</button></div></div>
    <div class="stack" style="gap:12px; margin-bottom:16px">
      ${plan.project.notes && plan.project.notes.length ? `<div class="callout warn">${ic('alert')}<div><b>Notes from reading the documents</b><ul>${plan.project.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul></div></div>` : ''}
      <div class="callout">${ic('info')}<div>Change anything before you approve. Quantities come from the documents and keep their source; ${missing ? `<b style="display:inline">${plural(missing, 'item')} need a quantity from you.</b>` : 'every quantity was found in the documents.'} Nothing counts toward progress until a report is approved.</div></div>
    </div>
    <section class="card"><div class="table-wrap"><table class="tbl">
      <thead><tr><th>Item</th><th class="hide-sm">Measured by</th><th>Quantity</th><th>Who reports it</th></tr></thead>
      <tbody>${plan.items.map(it => `<tr>
        <td><b>${esc(it.name)}</b><div class="small muted">${esc(it.source || '')}</div></td>
        <td class="hide-sm">${esc(METHOD[it.method] || it.method)}</td>
        <td>${it.method === 'done' ? '<span class="muted">—</span>' : `<div style="display:flex; gap:8px; align-items:center"><input class="input mono" id="pi-q-${it.id}" inputmode="decimal" value="${it.quantity != null ? it.quantity : ''}" placeholder="Enter" style="width:110px" aria-label="Quantity for ${esc(it.name)}"><span class="muted">${esc(it.unit)}</span></div>${it.quantity == null ? '<div class="err" style="margin-top:4px">Not in the documents</div>' : ''}`}</td>
        <td><select class="input" id="pi-w-${it.id}" aria-label="Who reports ${esc(it.name)}"><option value="">Nobody yet</option>${plan.team.map(p => `<option value="${p.id}" ${p.id === it.person_id ? 'selected' : ''}>${esc(p.name)} · ${esc(p.role_label)}</option>`).join('')}</select></td></tr>`).join('')}</tbody>
    </table></div><div class="card-foot">Approving locks this plan as the baseline that progress is measured against. Later changes go in as amendments.</div></section>
    <p class="err" id="planErr" hidden style="margin-top:12px"></p></div>`;
}
function readPlan() {
  return UI.plan.items.map(it => {
    const q = $('#pi-q-' + it.id), w = $('#pi-w-' + it.id), out = { id: it.id };
    if (q && q.value.trim()) out.quantity = num(q.value);
    if (w && w.value) out.person_id = +w.value;
    return out;
  });
}
async function startReading(prid, pages) {
  // Registered before the request: the plan.proposed event can arrive before the response does.
  const timer = setInterval(async () => {
    try { const pl = await api('GET', `/api/projects/${prid}/plan`); if (pl.project.plan_status === 'review') finishReading(prid); } catch { /* keep waiting */ }
  }, 1500);
  UI.reading.set(prid, { pages, timer });
  try { await api('POST', `/api/projects/${prid}/plan`); } catch (e) { clearInterval(timer); UI.reading.delete(prid); fail(e); }
}
async function finishReading(prid) {
  const rd = UI.reading.get(prid);
  if (!rd) return;
  clearInterval(rd.timer);
  UI.reading.delete(prid);
  await refresh();
  const pr = project(prid);
  const r = route();
  if (r.page === 'projects' && r.id === prid) location.hash = `#/projects/${prid}/plan`;
  else toast(`The plan for ${pr ? pr.name : 'the new project'} is ready for review.`, 'pend', { icon: 'file', href: `#/projects/${prid}/plan`, label: 'Review' });
}

/* ================= jobs ================= */
function jobsFiltered() {
  const f = UI.jobs;
  let js = S.jobs.slice();
  if (f.project !== 'all') js = js.filter(j => j.project_id === +f.project);
  if (f.show === 'unassigned') js = js.filter(j => !j.assignee_id);
  if (f.show === 'pending') js = js.filter(j => j.pending_pct > j.approved_pct);
  if (f.q) { const q = f.q.toLowerCase(); js = js.filter(j => `${j.name} ${j.tag} ${j.assignee_id ? nameOf(j.assignee_id) : ''}`.toLowerCase().includes(q)); }
  return js;
}
const jobsBody = (js) => js.map(j => jobRow(j, true)).join('') || '<tr><td colspan="6" class="empty">No jobs match.</td></tr>';
function renderJobs(r, view) {
  crumbs([['Jobs']]);
  const f = UI.jobs;
  view.innerHTML = `
    <div class="page-head"><div><h1>Jobs</h1><p>Everything being tracked, across projects. Progress counts approved work only; the striped part is waiting for approval.</p></div></div>
    <section class="card">
      <div class="toolbar">
        <label class="search">${ic('search')}<input class="input" id="jobsQ" type="search" placeholder="Search jobs or people" value="${esc(f.q)}" aria-label="Search jobs"></label>
        <select class="input" id="jobsProj" aria-label="Project"><option value="all">All projects</option>${S.projects.map(p => `<option value="${p.id}" ${String(p.id) === String(f.project) ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>
        <div class="seg" role="group" aria-label="Show">${[['all', 'All'], ['unassigned', 'Unassigned'], ['pending', 'Waiting on approval']].map(([v, l]) => `<button type="button" data-act="jobs-show" data-v="${v}" aria-pressed="${f.show === v}">${l}</button>`).join('')}</div>
      </div>
      <div class="table-wrap"><table class="tbl">${jobsHead}<tbody id="jobsBody">${jobsBody(jobsFiltered())}</tbody></table></div>
    </section>`;
}
function jobDrawer(id) {
  const j = job(id);
  if (!j) return null;
  const pr = project(j.project_id), w = j.assignee_id && person(j.assignee_id);
  const opts = S.people.filter(p => !isMe(p)).map(p => `<option value="${p.id}" ${p.id === j.assignee_id ? 'selected' : ''}>${esc(p.name)} · ${esc(p.role_label)}</option>`).join('');
  return `
    <div class="drawer-head">${pr ? projTile(pr) : ''}<div class="grow"><h2 id="dTitle">${esc(j.name)}</h2><p>${esc(pr ? pr.name : '')}</p><div class="pills">${pill(j.status[0], j.status[1])}</div></div><button class="iconbtn" data-act="close-drawer" aria-label="Close">${ic('x')}</button></div>
    <div class="drawer-body">
      <div class="section"><h3>Progress</h3>${bar(j.approved_pct, j.pending_pct)}
        <dl class="kv" style="margin-top:14px"><dt>Measured by</dt><dd>${esc(METHOD[j.method] || j.method)}</dd><dt>Quantity</dt><dd class="mono">${esc(j.qty_label)}</dd><dt>Last update</dt><dd>${esc(j.last_update ? fmtWhen(j.last_update) : 'Nothing yet')}</dd></dl></div>
      <div class="section"><h3>Assigned to</h3>
        ${w ? `<button class="row" style="padding:8px 0" data-act="open-person" data-id="${w.id}">${avatar(w)}<span class="grow"><span class="t">${esc(w.name)}</span><span class="s">${esc(w.role_label)}</span></span>${ic('chevron-right')}</button>` : '<p class="nobody" style="margin-bottom:10px">Nobody is reporting on this job.</p>'}
        <div style="display:flex; gap:8px; margin-top:8px"><select class="input" id="jobWho" aria-label="Assign to"><option value="">Nobody</option>${opts}</select><button class="btn btn-secondary" data-act="job-assign" data-job="${j.id}">${w ? 'Reassign' : 'Assign'}</button></div>
        <p class="small muted" style="margin-top:8px">Their Assistant starts tracking this job right away and drafts reports against it.</p></div>
      ${j.checklist && j.checklist.length ? `<div class="section"><h3>Evidence the Assistant asks for</h3>${bullets(j.checklist)}</div>` : ''}
    </div>
    <div class="drawer-foot">${pr ? `<a class="btn btn-secondary" href="#/projects/${pr.id}" data-act="close-drawer-go">Open project</a>` : ''}</div>`;
}

/* ================= people ================= */
function peopleFiltered() {
  const f = UI.people;
  let ps = S.people.slice();
  if (f.team !== 'All') ps = ps.filter(p => p.team === f.team);
  if (f.q) { const q = f.q.toLowerCase(); ps = ps.filter(p => `${p.name} ${p.role_label} ${p.company || ''} ${p.contact || ''}`.toLowerCase().includes(q)); }
  return ps;
}
function personRow(p) {
  return `<tr class="click" tabindex="0" data-person="${p.id}">
    <td><div class="who">${avatar(p)}<div><b>${esc(p.name)}${isMe(p) ? ' <span class="muted" style="display:inline">(you)</span>' : ''}</b><span>${esc(p.company || p.contact || p.team)}</span></div></div></td>
    <td>${esc(p.role_label)}<div class="small muted">${esc(p.team)}</div></td>
    <td>${activity(p)}</td>
    <td class="hide-sm">${linkPill(p)}</td>
    <td class="r mono">${jobsOf(p.id).length}</td>
    <td class="hide-sm muted">${esc(isMe(p) ? '—' : ago(p.last_active))}</td></tr>`;
}
const peopleBody = (ps) => ps.map(personRow).join('') || '<tr><td colspan="6" class="empty">Nobody matches.</td></tr>';
function renderPeople(r, view) {
  crumbs([['People']]);
  const f = UI.people;
  const teams = ['All', ...TEAMS.filter(t => S.people.some(p => p.team === t))];
  view.innerHTML = `
    <div class="page-head"><div><h1>People</h1><p>Each person has their own Assistant, matched to their role. People send photos, voice notes and files from the field app; their Assistant turns them into reports.</p></div>
      <div class="actions"><button class="btn btn-primary" data-act="add-person">${ic('user-plus')}Add person</button></div></div>
    <section class="card">
      <div class="toolbar">
        <label class="search">${ic('search')}<input class="input" id="peopleQ" type="search" placeholder="Search people" value="${esc(f.q)}" aria-label="Search people"></label>
        <div class="seg" role="group" aria-label="Team">${teams.map(t => `<button type="button" data-act="people-team" data-v="${esc(t)}" aria-pressed="${f.team === t}">${esc(t)}</button>`).join('')}</div>
      </div>
      <div class="table-wrap"><table class="tbl">
        <thead><tr><th>Person</th><th>Role</th><th>Assistant is…</th><th class="hide-sm">Field app</th><th class="r">Jobs</th><th class="hide-sm">Last update</th></tr></thead>
        <tbody id="peopleBody">${peopleBody(peopleFiltered())}</tbody></table></div>
    </section>`;
}
async function loadInputs(id) {
  try {
    const full = await api('GET', `/api/people/${id}`);
    UI.inputs[id] = full.inputs;
    if (UI.drawer && UI.drawer.type === 'person' && UI.drawer.id === id) renderDrawer(false);
  } catch { /* the drawer shows what the snapshot has */ }
}
const INPUT_IC = { photo: 'image', voice: 'mic', doc: 'file', text: 'chat' };
function inputRow(x) {
  const k = x.kind === 'photo' ? 'photo' : x.kind === 'voice' ? 'voice' : 'doc';
  const inner = `<span class="tile tile-pend">${ic(INPUT_IC[x.kind] || 'file')}</span><span class="grow"><span class="t" style="font-weight:500">${esc(x.text)}</span>${x.quote ? `<span class="s">“${esc(x.quote)}”</span>` : ''}</span><span class="small muted">${esc(fmtWhen(x.at))}</span>`;
  return `<li>${x.blob_id ? `<button class="row" style="padding:8px 0" data-act="evidence" data-blob="${x.blob_id}" data-kind="${k}" data-label="${esc(x.text || '')}">${inner}</button>` : `<div class="row" style="padding:8px 0">${inner}</div>`}</li>`;
}
function personDrawer(id) {
  const p = person(id);
  if (!p) return null;
  const head = `<div class="drawer-head">${avatar(p, 'lg')}<div class="grow"><h2 id="dTitle">${esc(p.name)}</h2><p>${esc(p.role_label)}${p.company ? ' · ' + esc(p.company) : ''}${p.where ? ' · ' + esc(p.where) : ''}</p><div class="pills">${linkPill(p)}</div></div><button class="iconbtn" data-act="close-drawer" aria-label="Close">${ic('x')}</button></div>`;
  if (isMe(p)) {
    return `${head}<div class="drawer-body"><div class="section"><p>You approve reports and plans. Your Assistant answers questions about any job from the records.</p></div></div>
      <div class="drawer-foot"><a class="btn btn-primary" href="#/ask" data-act="close-drawer-go">${ic('spark')}Ask about the work</a></div>`;
  }
  const js = jobsOf(id), appr = approverFor(p), first = firstName(p.name);
  const inputs = UI.inputs[id] || p.inputs;
  return `${head}
    <div class="drawer-body">
      <div class="section"><h3>Assistant</h3>
        <div style="font-weight:600; margin-bottom:8px">${activity(p)}</div>
        <p class="small muted">${esc(first)}’s Assistant turns photos, voice notes and files from the field app into draft reports, asks follow-up questions when something is unclear, and sends each report to ${esc(appr && !isMe(appr) ? appr.name : 'you')} for approval. It never approves anything on its own.</p></div>
      <div class="section"><h3>Recent from ${esc(first)}</h3>
        <ul class="list">${inputs.slice(0, 8).map(inputRow).join('') || '<li class="muted small">Nothing yet. Their first photo or note shows up here.</li>'}</ul></div>
      <div class="section"><h3>Jobs · ${js.length}</h3>
        <ul class="list">${js.map(j => `<li class="row" style="padding:10px 0; align-items:flex-start"><span class="grow"><span class="t">${esc(j.name)}</span><span style="display:block; margin:6px 0 2px">${bar(j.approved_pct, j.pending_pct, true)}</span><span class="s">${esc(j.qty_label)} · ${esc(j.status[1])}</span></span><button class="btn btn-ghost btn-sm" data-act="unassign" data-job="${j.id}">Remove</button></li>`).join('') || `<li class="muted small" style="padding-bottom:8px">No jobs yet. Add one so ${esc(first)}’s Assistant knows what to track.</li>`}</ul>
        <div style="margin-top:10px">${UI.addJob && UI.addJob.pid === id ? addJobPanel(p) : `<button class="btn btn-secondary btn-sm" data-act="aj-open" data-id="${id}">${ic('plus')}Add a job</button>`}</div></div>
      <div class="section"><h3>Role</h3>
        <fieldset class="roles" data-role-for="${id}"><legend class="vh">Role for ${esc(p.name)}</legend>${roleChoices(p.role, 'role-' + id)}</fieldset>
        <div style="display:flex; justify-content:flex-end; margin-top:10px"><button class="btn btn-primary btn-sm" data-act="role-save" data-id="${id}" disabled>Change role</button></div></div>
      <div class="section"><h3>Details</h3>
        <div class="stack" style="gap:12px">
          <label class="field"><span>Name</span><input id="pName" value="${esc(p.name)}" autocomplete="off"></label>
          <div class="grid2"><label class="field"><span>Phone or email</span><input id="pContact" value="${esc(p.contact || '')}" autocomplete="off"></label>
          <label class="field"><span>Company</span><input id="pCompany" value="${esc(p.company || '')}" autocomplete="off" placeholder="Contractors only"></label></div>
          <div class="actions"><button class="btn btn-secondary btn-sm" data-act="save-details" data-id="${id}">Save details</button><button class="btn btn-ghost btn-sm" data-act="new-link" data-id="${id}">${ic('link')}New field-app link</button></div>
        </div></div>
      <div class="section"><h3>Remove</h3><p class="small muted" style="margin-bottom:10px">Their Assistant stops taking messages and their link stops working. Approved history stays locked in the record.</p>
        <button class="btn btn-danger btn-sm" data-act="ask-remove" data-id="${id}">${ic('trash')}Remove ${esc(p.name)}</button></div>
    </div>`;
}
function roleChoices(cur, name) {
  return ROLE_ORDER.map(r => `<label class="choice"><input type="radio" name="${name}" value="${r}" ${r === cur ? 'checked' : ''}><span><b>${ROLES[r].label}</b><small><em>Assistant can:</em> ${esc(ROLES[r].can)}</small><small><em>Needs approval:</em> ${esc(ROLES[r].needs)}</small></span></label>`).join('');
}
function addJobPanel(p) {
  const A = UI.addJob;
  const others = S.jobs.filter(j => j.assignee_id !== p.id);
  const existing = others.length
    ? `<label class="field"><span>Job</span><select id="ajPick"><option value="">Choose a job…</option>${S.projects.map(pr => {
      const js = others.filter(j => j.project_id === pr.id);
      return js.length ? `<optgroup label="${esc(pr.name)}">${js.map(j => `<option value="${j.id}">${esc(j.name)}${j.assignee_id ? ` (now with ${esc(nameOf(j.assignee_id))})` : ' (unassigned)'}</option>`).join('')}</optgroup>` : '';
    }).join('')}</select></label>`
    : `<p class="muted small">Every job is already with ${esc(firstName(p.name))}.</p>`;
  const fresh = `<div class="grid2"><label class="field"><span>Project</span><select id="ajProj">${S.projects.map(pr => `<option value="${pr.id}" ${A.project === pr.id ? 'selected' : ''}>${esc(pr.name)}</option>`).join('')}</select></label>
      <label class="field"><span>Job name</span><input id="ajName" value="${esc(A.name || '')}" placeholder="Line B · 6-in water main"></label></div>
    <div class="field"><span>Measured by</span><div class="seg" role="group" aria-label="Measured by">${Object.entries(METHOD).map(([v, l]) => `<button type="button" aria-pressed="${A.method === v}" data-act="aj-method" data-v="${v}">${l}</button>`).join('')}</div></div>
    ${A.method === 'qty' ? `<div class="grid2"><label class="field"><span>Total</span><input id="ajTotal" class="mono" inputmode="decimal" value="${esc(A.total || '')}" placeholder="1,200"></label><label class="field"><span>Unit</span><select id="ajUnit">${['LF', 'EA', 'SY', 'CY', 'TON', 'sheets'].map(u => `<option ${A.unit === u ? 'selected' : ''}>${u}</option>`).join('')}</select></label></div>` : ''}`;
  return `<div class="panel" data-keep><h4>${ic('plus')}Add a job for ${esc(firstName(p.name))}</h4>
    <div class="seg" role="group" aria-label="Job source"><button type="button" aria-pressed="${A.mode === 'existing'}" data-act="aj-mode" data-v="existing">Existing job</button><button type="button" aria-pressed="${A.mode === 'new'}" data-act="aj-mode" data-v="new">New job</button></div>
    ${A.mode === 'existing' ? existing : fresh}
    <p class="err" id="ajErr" hidden></p>
    <div class="panel-acts"><button class="btn btn-secondary btn-sm" data-act="aj-cancel">Cancel</button><button class="btn btn-primary btn-sm" data-act="aj-save">${ic('plus')}Add job</button></div></div>`;
}
function keepAJ() {
  const A = UI.addJob;
  if (!A || A.mode !== 'new') return;
  const n = $('#ajName'), t = $('#ajTotal'), u = $('#ajUnit'), pr = $('#ajProj');
  if (n) A.name = n.value; if (t) A.total = t.value; if (u) A.unit = u.value; if (pr) A.project = +pr.value;
}
function rerenderDrawer() { renderDrawer(true); }

/* ----- add a person ----- */
let H = null;
function openHire() {
  H = { jobs: new Set() };
  modal({
    title: 'Add a person', sub: 'They get their own Assistant, matched to their role, and a private link to the field app.', wide: true,
    body: `<div class="grid2"><label class="field"><span>Name</span><input id="hName" autocomplete="off" placeholder="Priya Raman"><span class="err" id="hErr" hidden>Add a name.</span></label>
        <label class="field"><span>Phone or email</span><input id="hContact" autocomplete="off" placeholder="(555) 201-3344"></label></div>
      <label class="field"><span>Company</span><input id="hCompany" autocomplete="off" placeholder="Contractors only"></label>
      <fieldset class="field"><legend>Role</legend><div class="roles">${roleChoices('crew', 'hRole')}</div></fieldset>
      ${S.jobs.length ? `<fieldset class="field"><legend>Jobs to start with (optional)</legend><div class="pickgrid">${S.jobs.map(j => `<button type="button" class="pick" aria-pressed="false" data-act="h-job" data-job="${j.id}"><span class="grow"><b>${esc(j.name)}</b><span>${esc((project(j.project_id) || {}).short || '')} · ${j.assignee_id ? `now with ${esc(nameOf(j.assignee_id))}` : 'unassigned'}</span></span><span class="tick">${ic('check')}</span></button>`).join('')}</div></fieldset>` : ''}`,
    foot: `<button class="btn btn-secondary" data-act="close-modal">Cancel</button><button class="btn btn-primary" data-act="hire-go">${ic('user-plus')}Add person</button>`,
  });
}
async function hire(btn) {
  const name = $('#hName').value.trim();
  if (!name) { $('#hErr').hidden = false; $('#hName').focus(); return; }
  const role = ($('input[name="hRole"]:checked') || {}).value || 'crew';
  const contact = $('#hContact').value.trim();
  btn.disabled = true;
  let res;
  try { res = await api('POST', '/api/people', { name, role, contact: contact || null, company: $('#hCompany').value.trim() || null, job_ids: [...H.jobs] }); }
  catch (e) { btn.disabled = false; fail(e); return; }
  await refresh();
  linkModal(`${res.person.name} is set up`, res.field_link, contact, res.person.name);
}
function linkModal(title, link, contact, name) {
  modal({
    title, sub: 'Send this private link. It opens their Assistant in the field app on a phone or laptop.',
    body: `<div class="linkbox"><code id="fieldLink">${esc(link)}</code></div>
      <p class="small muted">Anyone with the link can send to this Assistant, so share it only with ${esc(name)}${contact ? ` (${esc(contact)})` : ''}. You can make a new one from their profile at any time.</p>`,
    foot: `<button class="btn btn-secondary" data-act="copy-link">${ic('link')}Copy link</button><button class="btn btn-primary" data-act="close-modal">Done</button>`,
  });
}

/* ----- new project ----- */
let NP = null;
function openNewProject() {
  NP = { step: 1, name: '', client: '', types: new Set(['Drainage']), team: new Set(S.people.filter(p => p.role === 'pm').map(p => p.id)), files: [] };
  renderNP();
}
function readNP() { const n = $('#npName'), c = $('#npClient'); if (n) NP.name = n.value.trim(); if (c) NP.client = c.value.trim(); }
function renderNP() {
  const steps = `<ol class="steps" aria-label="Steps">${['Details', 'Team', 'Documents'].map((s, k) => `<li class="${k + 1 === NP.step ? 'on' : k + 1 < NP.step ? 'done' : ''}" ${k + 1 === NP.step ? 'aria-current="step"' : ''}>${k + 1}. ${s}</li>`).join('')}</ol>`;
  let body = steps, foot = '';
  if (NP.step === 1) {
    body += `<label class="field"><span>Project name</span><input id="npName" value="${esc(NP.name)}" autocomplete="off" placeholder="Elm Street Drainage Improvements"><span class="err" id="npErr" hidden>Give the project a name.</span></label>
      <label class="field"><span>Client or agency</span><input id="npClient" value="${esc(NP.client)}" autocomplete="off" placeholder="City of Oak Hollow"></label>
      <fieldset class="field"><legend>Kind of work</legend><div class="chips">${['Water', 'Sewer', 'Drainage', 'Paving'].map(t => `<button type="button" class="chipbtn" aria-pressed="${NP.types.has(t)}" data-act="np-type" data-v="${t}">${NP.types.has(t) ? ic('check') : ''}${t}</button>`).join('')}</div></fieldset>`;
    foot = `<button class="btn btn-secondary" data-act="close-modal">Cancel</button><button class="btn btn-primary" data-act="np-next">Next${ic('arrow')}</button>`;
  } else if (NP.step === 2) {
    body += `<p class="muted">Everyone you pick can see this project’s scope documents and nothing else. You can change the team later.</p>
      <div class="pickgrid">${S.people.filter(p => !isMe(p)).map(p => `<button type="button" class="pick" aria-pressed="${NP.team.has(p.id)}" data-act="np-team" data-id="${p.id}">${avatar(p, 'sm')}<span class="grow"><b>${esc(p.name)}</b><span>${esc(p.company || p.role_label)}</span></span><span class="tick">${ic('check')}</span></button>`).join('')}</div>`;
    foot = `<button class="btn btn-secondary left" data-act="np-back">${ic('chevron-left')}Back</button><button class="btn btn-primary" data-act="np-next">Next${ic('arrow')}</button>`;
  } else {
    body += `<label class="dropzone" id="drop" for="npFiles">${ic('upload')}<b>Drop plans, specs and bid forms here</b><span>or click to choose files. PDFs are read for quantities; other files are kept with the project.</span><input type="file" id="npFiles" class="vh" multiple></label>
      ${NP.files.length ? `<ul class="files">${NP.files.map((f, k) => `<li>${ic('file')}<span class="grow">${esc(f.name)}</span><span class="small muted">${(f.file.size / 1e6).toFixed(1)} MB</span><button class="iconbtn" data-act="np-unfile" data-k="${k}" aria-label="Remove ${esc(f.name)}">${ic('x')}</button></li>`).join('')}</ul>` : ''}
      <div><button type="button" class="btn btn-ghost btn-sm" data-act="np-sample">${ic('file')}Use the sample bid form</button></div>
      <p class="small muted">Files already uploaded to another project are recognised and skipped. You can also start without documents and enter quantities yourself.</p>`;
    foot = `<button class="btn btn-secondary left" data-act="np-back">${ic('chevron-left')}Back</button><button class="btn btn-primary" data-act="np-create">${ic('check')}Create project</button>`;
  }
  modal({ title: 'New project', sub: ['Name the project and the kind of work.', 'Pick who works on it.', 'Add the scope documents. The Assistant reads them and proposes a plan for you to approve.'][NP.step - 1], body, foot, wide: NP.step === 2 });
  if (NP.step === 3) wireDrop();
}
function wireDrop() {
  const drop = $('#drop'), inp = $('#npFiles');
  const add = (files) => { [...files].forEach(f => NP.files.push({ name: f.name, file: f })); renderNP(); };
  inp.addEventListener('change', () => add(inp.files));
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => add(e.dataTransfer.files));
}
async function createProject(btn) {
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>Creating…';
  let pr, docs = [];
  try {
    pr = await api('POST', '/api/projects', { name: NP.name, client: NP.client || null, work_types: [...NP.types], team: [...NP.team] });
    if (NP.files.length) {
      const fd = new FormData();
      NP.files.forEach(f => fd.append('files', f.file, f.name));
      docs = (await api('POST', `/api/projects/${pr.id}/documents`, fd, true)).documents;
    }
  } catch (e) { btn.disabled = false; btn.innerHTML = `${ic('check')}Create project`; fail(e); return; }
  const ready = docs.filter(d => d.status === 'ready');
  const pages = ready.reduce((s, d) => s + (d.pages || 0), 0);
  const dups = docs.length - ready.length;
  closeModal();
  NP = null;
  await startReading(pr.id, pages);
  await refresh();
  location.hash = `#/projects/${pr.id}`;
  toast(`${pr.name} created.${pages ? ` Reading ${plural(pages, 'page')}.` : ''}${dups ? ` ${plural(dups, 'duplicate file')} skipped.` : ''}`, 'ok');
}

/* ----- new job from a project page ----- */
function openNewJob(prid) {
  modal({
    title: 'Add a job', sub: (project(prid) || {}).name || '',
    body: `<label class="field"><span>Job name</span><input id="njName" autocomplete="off" placeholder="Line B · 6-in water main"></label>
      <div class="grid2"><label class="field"><span>Measured by</span><select id="njMethod">${Object.entries(METHOD).map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></label>
        <label class="field"><span>Who reports it</span><select id="njWho"><option value="">Nobody yet</option>${S.people.filter(p => !isMe(p)).map(p => `<option value="${p.id}">${esc(p.name)} · ${esc(p.role_label)}</option>`).join('')}</select></label></div>
      <div class="grid2" id="njQty"><label class="field"><span>Total</span><input id="njTotal" class="mono" inputmode="decimal" placeholder="1,200"></label><label class="field"><span>Unit</span><select id="njUnit">${['LF', 'EA', 'SY', 'CY', 'TON', 'sheets'].map(u => `<option>${u}</option>`).join('')}</select></label></div>
      <p class="err" id="njErr" hidden></p>`,
    foot: `<button class="btn btn-secondary" data-act="close-modal">Cancel</button><button class="btn btn-primary" data-act="nj-save" data-proj="${prid}">${ic('plus')}Add job</button>`,
  });
}

/* ================= ask ================= */
function chatHTML() {
  if (!UI.chat.length) {
    return `<div class="empty" style="padding-top:40px">${ic('spark')}Ask about a job, a person or a report. Answers cite the records they come from.</div>
      <div class="chips" style="justify-content:center">${S.suggested_questions.map(q => `<button class="chipbtn" data-act="ask-q" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>`;
  }
  return UI.chat.map(m => m.me ? `<div class="msg me">${esc(m.text)}</div>`
    : `<div class="msg bot"><span class="tile">${m.wait ? '<span class="spinner" aria-hidden="true"></span>' : ic('spark')}</span><div><span class="by">Assistant${m.by === 'llm' ? ' · local model' : m.by === 'rules' ? ' · rules engine' : ''}</span>${esc(m.text)}${m.sources && m.sources.length ? `<div class="chips">${m.sources.map(s => `<span class="tag">${esc(s)}</span>`).join('')}</div>` : ''}</div></div>`).join('');
}
function renderAsk(r, view) {
  crumbs([['Ask about the work']]);
  view.innerHTML = `
    <div class="page-head"><div><h1>Ask about the work</h1><p>Answers come from the job records, reports and locked history, with sources. ${S.assistant.mode === 'llm' ? `Running on ${esc(S.assistant.label.split(' · ').slice(1).join(' · '))}.` : 'Running on the rules engine; set a local model for open-ended questions.'}</p></div>
      ${UI.chat.length ? '<div class="actions"><button class="btn btn-ghost" data-act="clear-chat">Clear</button></div>' : ''}</div>
    <section class="card"><div class="chat" id="chat" aria-live="polite">${chatHTML()}</div>
      <form class="composer" id="askForm"><input class="input" id="askInput" placeholder="Ask about a job, a person or a report" autocomplete="off" aria-label="Question"><button class="btn btn-primary" type="submit">${ic('send')}Ask</button></form></section>`;
}
async function ask(q) {
  q = q.trim();
  if (!q) return;
  UI.chat.push({ me: true, text: q }, { wait: true, text: 'Looking through the records…' });
  const paint = () => { const c = $('#chat'); if (c) { c.innerHTML = chatHTML(); c.lastElementChild && c.lastElementChild.scrollIntoView({ block: 'nearest' }); } };
  paint();
  try { const a = await api('POST', '/api/ask', { question: q }); UI.chat[UI.chat.length - 1] = { text: a.answer, sources: a.sources, by: a.answered_by }; }
  catch (e) { UI.chat[UI.chat.length - 1] = { text: e.message }; }
  paint();
}

/* ================= evidence ================= */
function openEvidence(t) {
  const { blob, kind, label } = t.dataset;
  let body;
  if (!blob) body = `<p>${esc(label)}</p><p class="small muted">This came in as text, so there is no file to show.</p>`;
  else if (kind === 'photo') body = `<img class="evimg" src="/api/files/${blob}" alt="${esc(label)}">`;
  else if (kind === 'voice') body = `<audio controls src="/api/files/${blob}" style="width:100%"></audio>`;
  else body = `<iframe class="evpdf" src="/api/files/${blob}" title="${esc(label)}"></iframe>`;
  modal({ title: label || 'File', sub: 'The original file, exactly as it was sent.', body, wide: true, foot: `${blob ? `<a class="btn btn-ghost left" href="/api/files/${blob}" target="_blank" rel="noopener">${ic('external')}Open original</a>` : ''}<button class="btn btn-primary" data-act="close-modal">Done</button>` });
}

/* ================= actions ================= */
const ACTS = {
  'close-modal': () => closeModal(),
  'close-drawer': () => closeDrawer(),
  'close-drawer-go': (t) => { closeDrawer(); location.hash = t.getAttribute('href'); },
  'new-project': () => openNewProject(),
  'add-person': () => openHire(),
  'open-person': (t) => { closeModal(); openDrawer('person', +t.dataset.id); },
  'jobs-unassigned': () => { UI.jobs.show = 'unassigned'; UI.jobs.project = 'all'; location.hash = '#/jobs'; },
  'jobs-show': (t) => { UI.jobs.show = t.dataset.v; render(true); },
  'people-team': (t) => { UI.people.team = t.dataset.v; render(true); },
  'evidence': (t) => openEvidence(t),

  'approve': (t) => confirmApprove([+t.dataset.id]),
  'approve-routine': () => confirmApprove(waiting().filter(i => i.group === 'routine').map(i => i.id)),
  'do-approve': (t) => doApprove(t.dataset.ids.split(',').map(Number), t),
  'ask-item': (t) => { UI.panel = { id: +t.dataset.id, kind: 'ask' }; render(true); const q = $('#qText'); if (q) q.focus(); },
  'req-changes': (t) => { UI.panel = { id: +t.dataset.id, kind: 'changes' }; render(true); const n = $('#cNote'); if (n) n.focus(); },
  'panel-cancel': () => { UI.panel = null; render(true); },
  'send-q': async (t) => {
    const q = t.dataset.q || ($('#qText') || {}).value || '';
    if (!q.trim()) { $('#qText').focus(); return; }
    try { await api('POST', `/api/reports/${t.dataset.id}/questions`, { text: q.trim() }); } catch (e) { fail(e); return; }
    UI.panel = null; toast('Question sent. It’s pinned to the report and shows up on their phone.', 'ok', { icon: 'send' });
    await refresh(); render(true);
  },
  'send-changes': async (t) => {
    const id = +t.dataset.id;
    const items = $$('.checks input:checked').map(c => c.value);
    const next = waiting().find(i => i.id !== id);
    try { await api('POST', `/api/reports/${id}/changes`, { items, note: $('#cNote').value.trim() || null }); } catch (e) { fail(e); return; }
    UI.panel = null; toast('Sent back with the list of changes.', 'ok', { icon: 'send' });
    await refresh();
    location.hash = next ? `#/inbox/${next.id}` : '#/inbox';
    render(true);
  },

  'propose-plan': async (t) => { await startReading(+t.dataset.proj, 0); await refresh(); render(true); },
  'plan-approve': (t) => {
    const items = readPlan();
    const blank = UI.plan.items.filter(it => it.method === 'qty' && !items.find(x => x.id === it.id).quantity).length;
    confirmBox({
      title: 'Approve and lock this plan?', sub: UI.plan.project.name,
      body: `<p>${plural(items.length, 'item')} become the baseline that progress is measured against. Each person’s Assistant starts tracking their jobs right away.</p>${blank ? `<div class="callout warn">${ic('alert')}<div>${plural(blank, 'item')} still ${blank === 1 ? 'has' : 'have'} no quantity.</div></div>` : ''}`,
      ok: `${ic('lock')}Approve plan`, okClass: 'btn-approve', act: 'do-plan-approve', data: { proj: t.dataset.proj },
    });
    UI.pendingPlan = items;
  },
  'do-plan-approve': async (t) => {
    const prid = +t.dataset.proj;
    t.disabled = true;
    let res;
    try { res = await api('POST', `/api/projects/${prid}/plan/approve`, { items: UI.pendingPlan }); }
    catch (e) { closeModal(); const er = $('#planErr'); if (er) { er.hidden = false; er.textContent = e.message; er.scrollIntoView({ block: 'nearest' }); } else fail(e); return; }
    closeModal(); UI.plan = null;
    await refresh();
    location.hash = `#/projects/${prid}`;
    toast(`Plan approved and locked as ${res.fingerprint}. ${plural(res.jobs.length, 'job')} created; progress tracking starts now.`, 'ok', { icon: 'lock' });
  },
  'new-job': (t) => openNewJob(+t.dataset.proj),
  'nj-save': async (t) => {
    const method = $('#njMethod').value, name = $('#njName').value.trim(), who = $('#njWho').value;
    try {
      await api('POST', '/api/jobs', { project_id: +t.dataset.proj, name, method, total: method === 'qty' ? num($('#njTotal').value) : null, unit: $('#njUnit').value, assignee_id: who ? +who : null });
    } catch (e) { const er = $('#njErr'); er.hidden = false; er.textContent = e.message; return; }
    closeModal(); toast(`${name} added.`); await refresh();
  },

  'amend': (t) => { UI.amend = +t.dataset.entry; render(true); const el = $('#amW'); if (el) el.focus(); },
  'amend-cancel': () => { UI.amend = null; render(true); },
  'amend-preview': (t) => {
    const prid = +t.dataset.proj, eid = +t.dataset.entry;
    const h = UI.projects[prid].history.find(x => x.id === eid);
    const text = $('#amT').value.trim(), reason = $('#amW').value.trim(), d = $('#amD');
    if (!reason) { $('#amErr').hidden = false; $('#amW').focus(); return; }
    UI.pendingAmend = { prid, eid, text, reason, delta: d && d.value.trim() ? num(d.value) : null };
    confirmBox({
      title: 'Add this amendment?', sub: 'The original stays readable, struck through. Both are locked.',
      body: `<div class="card"><div class="entry"><div class="entry-text struck">${esc(h.text)}</div><div class="amend"><div class="entry-top">${ic('pen')}<span>Amended by you · now</span></div><div class="entry-text">${esc(text)}</div><div class="small muted">Reason: ${esc(reason)}</div></div></div></div>`,
      ok: `${ic('lock')}Amend and lock`, act: 'do-amend',
    });
  },
  'do-amend': async (t) => {
    const a = UI.pendingAmend;
    t.disabled = true;
    try { await api('POST', `/api/ledger/${a.eid}/amend`, { text: a.text, reason: a.reason, delta: a.delta }); } catch (e) { t.disabled = false; fail(e); return; }
    closeModal(); UI.amend = null;
    toast('Amendment added. The original stays on the record.', 'ok', { icon: 'pen' });
    await refresh(); render(true);
  },

  'np-type': (t) => { readNP(); const v = t.dataset.v; if (NP.types.has(v)) NP.types.delete(v); else NP.types.add(v); renderNP(); },
  'np-team': (t) => { const id = +t.dataset.id; if (NP.team.has(id)) NP.team.delete(id); else NP.team.add(id); t.setAttribute('aria-pressed', NP.team.has(id)); },
  'np-next': () => { if (NP.step === 1) { readNP(); if (!NP.name) { $('#npErr').hidden = false; $('#npName').focus(); return; } } NP.step++; renderNP(); },
  'np-back': () => { NP.step--; renderNP(); },
  'np-unfile': (t) => { NP.files.splice(+t.dataset.k, 1); renderNP(); },
  'np-sample': async () => {
    try { const b = await (await fetch('/static/samples/elm-street-bid-form.pdf')).blob(); NP.files.push({ name: 'Elm St bid form.pdf', file: new File([b], 'Elm St bid form.pdf', { type: 'application/pdf' }) }); renderNP(); }
    catch (e) { fail(e); }
  },
  'np-create': (t) => createProject(t),

  'h-job': (t) => { const id = +t.dataset.job; if (H.jobs.has(id)) H.jobs.delete(id); else H.jobs.add(id); t.setAttribute('aria-pressed', H.jobs.has(id)); },
  'hire-go': (t) => hire(t),
  'copy-link': async () => {
    const txt = $('#fieldLink').textContent;
    try { await navigator.clipboard.writeText(txt); toast('Link copied.', 'ok', { icon: 'link' }); }
    catch { const r = document.createRange(); r.selectNodeContents($('#fieldLink')); getSelection().removeAllRanges(); getSelection().addRange(r); toast('Press Ctrl+C or ⌘C to copy.', 'pend', { icon: 'link' }); }
  },
  'new-link': async (t) => {
    const p = person(+t.dataset.id);
    try { const r = await api('POST', `/api/people/${p.id}/link`); linkModal(`New link for ${p.name}`, r.field_link, p.contact, p.name); } catch (e) { fail(e); }
  },

  'job-assign': async (t) => {
    const who = $('#jobWho').value;
    try { await api('PATCH', `/api/jobs/${t.dataset.job}`, { assignee_id: who ? +who : null }); } catch (e) { fail(e); return; }
    toast(who ? `Assigned to ${nameOf(+who)}.` : 'Job is now unassigned.'); await refresh(); rerenderDrawer();
  },
  'unassign': async (t) => {
    try { await api('PATCH', `/api/jobs/${t.dataset.job}`, { assignee_id: null }); } catch (e) { fail(e); return; }
    toast('Taken off the job.'); await refresh(); rerenderDrawer();
  },
  'aj-open': (t) => { UI.addJob = { pid: +t.dataset.id, mode: 'existing', method: 'qty', unit: 'LF', project: (S.projects[0] || {}).id }; rerenderDrawer(); },
  'aj-mode': (t) => { keepAJ(); UI.addJob.mode = t.dataset.v; rerenderDrawer(); },
  'aj-method': (t) => { keepAJ(); UI.addJob.method = t.dataset.v; rerenderDrawer(); },
  'aj-cancel': () => { UI.addJob = null; rerenderDrawer(); },
  'aj-save': async () => {
    const A = UI.addJob, err = $('#ajErr');
    try {
      if (A.mode === 'existing') {
        const jid = +($('#ajPick') || {}).value;
        if (!jid) { err.hidden = false; err.textContent = 'Choose a job first.'; return; }
        await api('PATCH', `/api/jobs/${jid}`, { assignee_id: A.pid });
      } else {
        keepAJ();
        await api('POST', '/api/jobs', { project_id: A.project, name: (A.name || '').trim(), method: A.method, total: A.method === 'qty' ? num(A.total) : null, unit: A.unit || 'LF', assignee_id: A.pid });
      }
    } catch (e) { err.hidden = false; err.textContent = e.message; return; }
    UI.addJob = null; toast(`Job added for ${nameOf(A.pid)}.`); await refresh(); rerenderDrawer();
  },
  'role-save': async (t) => {
    const id = +t.dataset.id, r = $(`input[name="role-${id}"]:checked`);
    if (!r) return;
    try { await api('PATCH', `/api/people/${id}`, { role: r.value }); } catch (e) { fail(e); return; }
    toast(`${nameOf(id)} is now ${ROLES[r.value].label.toLowerCase()}. Their Assistant’s permissions changed with the role.`);
    await refresh(); rerenderDrawer();
  },
  'save-details': async (t) => {
    try { await api('PATCH', `/api/people/${t.dataset.id}`, { name: $('#pName').value.trim(), contact: $('#pContact').value.trim(), company: $('#pCompany').value.trim() }); } catch (e) { fail(e); return; }
    toast('Details saved.'); await refresh(); rerenderDrawer();
  },
  'ask-remove': (t) => {
    const p = person(+t.dataset.id), n = jobsOf(p.id).length;
    confirmBox({
      title: `Remove ${p.name}?`, sub: 'This can’t be undone, but nothing on the record is lost.',
      body: `<p>Their Assistant stops taking messages and their field-app link stops working. Approved history stays locked in the record.${n ? ` ${plural(n, 'job')} will need someone new.` : ''}</p>`,
      ok: `${ic('trash')}Remove`, okClass: 'btn-danger', act: 'do-remove', data: { id: p.id },
    });
  },
  'do-remove': async (t) => {
    const id = +t.dataset.id, name = nameOf(id);
    t.disabled = true;
    let res;
    try { res = await api('DELETE', `/api/people/${id}`); } catch (e) { t.disabled = false; fail(e); return; }
    closeModal(); closeDrawer();
    toast(`${name} removed.${res.jobs_needing_someone.length ? ` ${plural(res.jobs_needing_someone.length, 'job')} need someone new.` : ''}`, 'ok');
    await refresh();
  },

  'ask-q': (t) => ask(t.dataset.q),
  'clear-chat': () => { UI.chat = []; render(true); },
};

document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-act]');
  if (t && ACTS[t.dataset.act]) { e.preventDefault(); ACTS[t.dataset.act](t, e); return; }
  const tr = e.target.closest('tr[data-href], tr[data-person], tr[data-job]');
  if (tr && !e.target.closest('a,button,input,select')) openRow(tr);
  if (e.target.closest('.nav a, .brand')) setNav(false);
});
function openRow(tr) {
  if (tr.dataset.href) location.hash = tr.dataset.href;
  else if (tr.dataset.person) openDrawer('person', +tr.dataset.person);
  else openDrawer('job', +tr.dataset.job);
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.matches('tr[tabindex]')) { openRow(e.target); return; }
  if (e.key !== 'Escape') return;
  if ($('#modalRoot').innerHTML) closeModal();
  else if (UI.drawer) closeDrawer();
  else setNav(false);
});
document.addEventListener('input', (e) => {
  if (e.target.id === 'jobsQ') { UI.jobs.q = e.target.value; $('#jobsBody').innerHTML = jobsBody(jobsFiltered()); }
  if (e.target.id === 'peopleQ') { UI.people.q = e.target.value; $('#peopleBody').innerHTML = peopleBody(peopleFiltered()); }
});
document.addEventListener('change', (e) => {
  if (e.target.id === 'jobsProj') { UI.jobs.project = e.target.value; $('#jobsBody').innerHTML = jobsBody(jobsFiltered()); }
  if (e.target.id === 'njMethod') $('#njQty').hidden = e.target.value !== 'qty';
  const fs = e.target.closest('fieldset[data-role-for]');
  if (fs) { const id = +fs.dataset.roleFor, btn = $(`[data-act="role-save"][data-id="${id}"]`); if (btn) btn.disabled = e.target.value === person(id).role; }
});
document.addEventListener('submit', (e) => {
  if (e.target.id !== 'askForm') return;
  e.preventDefault();
  const i = $('#askInput');
  ask(i.value); i.value = '';
});
function setNav(open) { document.body.classList.toggle('nav-open', open); $('#menuBtn').setAttribute('aria-expanded', open); }
$('#menuBtn').addEventListener('click', () => setNav(!document.body.classList.contains('nav-open')));
$('#sideScrim').addEventListener('click', () => setNav(false));
window.addEventListener('hashchange', () => { UI.panel = null; UI.amend = null; render(true); });
small.addEventListener('change', () => render(true));

/* ================= live updates ================= */
let refreshTimer = null;
function scheduleRefresh(ms = 400) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, ms); }
async function refresh() {
  try { S = await api('GET', '/api/office'); } catch { return; }
  render(false);
}
function setLive(on) {
  const el = $('#live');
  el.classList.toggle('off', !on);
  el.lastElementChild.textContent = on ? 'Live' : 'Reconnecting';
}
function onEvent(ev) {
  if (!S) return;
  const d = ev.data || {};
  if (ev.text) {
    const chip = d.blob_id ? { label: d.label || 'File', kind: d.kind || 'doc', blob_id: d.blob_id } : null;
    S.log.unshift({ id: ev.id, type: ev.type, at: ev.at, text: ev.text, person_id: ev.person_id, chip });
    S.log = S.log.slice(0, 60);
    UI.fresh.add(ev.id);
    setTimeout(() => UI.fresh.delete(ev.id), 3000);
    const feed = $('#feed');
    if (feed) feed.innerHTML = feedHTML();
  }
  switch (ev.type) {
    case 'assistant.status': {
      const p = person(ev.person_id);
      if (p) { p.activity = { kind: d.kind, text: d.label }; $$(`[data-activity="${p.id}"]`).forEach(el => { el.innerHTML = activityInner(p); }); }
      return;
    }
    case 'report.submitted':
      toast(ev.text, d.group === 'flagged' ? 'bad' : d.group === 'attention' ? 'warn' : 'pend', { href: `#/inbox/${d.report_id}`, label: 'Review' });
      break;
    case 'person.online':
      toast(ev.text, 'ok', { icon: 'link' });
      break;
    case 'plan.proposed':
      finishReading(d.project_id);
      break;
    default:
  }
  scheduleRefresh();
}
function connect() {
  const es = new EventSource(`/api/events/stream?after=${S.last_event_id}`);
  es.onopen = () => setLive(true);
  es.onerror = () => setLive(false);
  es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (err) { console.error(err); } };
}

/* ================= boot ================= */
const STATIC_PAGES = new Set(['ask']); // nothing live to show; a refresh would only clear the question box
const PAGES = { overview: renderOverview, inbox: renderInbox, projects: renderProjects, jobs: renderJobs, people: renderPeople, ask: renderAsk };
(async function boot() {
  try { S = await api('GET', '/api/office'); }
  catch (e) { if (e.status !== 401) $('#view').innerHTML = `<p class="loading">${esc(e.message)}</p>`; return; }
  render(true);
  connect();
  setInterval(() => render(false), 60000); // keeps "5 min ago" honest
})();
})();
