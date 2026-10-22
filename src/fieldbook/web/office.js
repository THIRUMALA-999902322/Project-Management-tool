/* Fieldbook Office: the owner's live office, driven by /api/office and /api/events/stream.
   The server is the source of truth. Actions call the API, play their animation, then refresh.
   Live events from the field (photos arriving, reports sent) animate as they come in. */
(() => {
'use strict';

/* ================= helpers ================= */
const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const sleep = (ms) => new Promise(r => setTimeout(r, reduce ? Math.min(ms, 20) : ms));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => Number(n).toLocaleString('en-US', { maximumFractionDigits: 1 });
const ic = (name, cls = '') => `<svg class="ic ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const asstBadge = () => `<span class="asst"><svg aria-hidden="true"><use href="#i-asst"/></svg></span>`;
const make = (tag, cls, html) => { const el = document.createElement(tag); if (cls) el.className = cls; if (html != null) el.innerHTML = html; return el; };
const fx = $('#fx');
function docRect(el) { const r = el.getBoundingClientRect(); return { left: r.left + scrollX, top: r.top + scrollY, width: r.width, height: r.height, right: r.right + scrollX, bottom: r.bottom + scrollY, cx: r.left + scrollX + r.width / 2, cy: r.top + scrollY + r.height / 2 }; }
function fxSize() { fx.style.height = document.documentElement.scrollHeight + 'px'; }
const isToday = (iso) => new Date(iso).toDateString() === new Date().toDateString();
const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const fmtDay = (iso) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
const fmtWhen = (iso) => iso ? (isToday(iso) ? fmtTime(iso) : `${fmtDay(iso)}, ${fmtTime(iso)}`) : '';

/* ================= API ================= */
class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
async function api(method, path, body, form) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (form) opts.body = body;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  if (res.status === 401) { showSignIn(); throw new ApiError(401, 'Sign in with your login link.'); }
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const d = data && data.detail;
    throw new ApiError(res.status, typeof d === 'string' ? d : 'Something went wrong. Try again.');
  }
  return data;
}
function fail(e) { toast(e.message || String(e), 'alert'); }
function showSignIn() {
  if ($('.signin')) return;
  document.body.appendChild(make('div', 'signin', `<div class="signin__card"><h1 class="stage__title">Open your login link</h1><p class="explain">Fieldbook signs you in with a private link. Open the one you were given, or create one on the server with <code>fieldbook link owner</code>.</p></div>`));
}

/* ================= reference copy (the server enforces permissions) ================= */
const ROLES = {
  owner: { label: 'Owner', room: 'owner' },
  pm: { label: 'Project manager', room: 'pm', can: 'Checks each report against the original files and the plan, then drafts a short review with flags and questions.', needs: 'Approving. The Assistant never approves on its own.' },
  engineer: { label: 'Design engineer', room: 'design', can: 'Tracks calcs, design decisions and spec sections, and drafts answers to RFIs.', needs: 'Anything that leaves the company.' },
  cad: { label: 'CAD tech', room: 'design', can: 'Counts sheets drafted against the sheet list and files markups where they belong.', needs: 'Sheet counts, before they count toward the submittal.' },
  qaqc: { label: 'QA/QC reviewer', room: 'qaqc', can: 'Logs review comments by sheet and checks that each one is closed out.', needs: 'Marking a set ready to submit.' },
  crew: { label: 'Crew lead', room: 'trailer', field: true, can: 'Turns photos, voice notes and texts into a daily progress report. Asks “Which stations?” when it can’t tell.', needs: 'Every quantity, before it counts toward progress.' },
  inspector: { label: 'Inspector', room: 'trailer', field: true, can: 'Logs inspections, tests and CCTV runs with photos, and flags anything that doesn’t match the plan.', needs: 'Pass or fail results and quantities.' },
  contractor: { label: 'Contractor', room: 'gate', field: true, can: 'Reports on their own scope only. Can’t see other jobs, people or prices.', needs: 'Every quantity and every change.' },
};
const ROLE_ORDER = ['pm', 'engineer', 'cad', 'qaqc', 'crew', 'inspector', 'contractor'];
const ROOM_LABEL = { owner: 'Owner’s office', pm: 'Project manager’s office', design: 'Design studio', qaqc: 'QA/QC', trailer: 'Site trailer', gate: 'Contractor gate' };
const ROOMS = ['owner', 'pm', 'design', 'qaqc', 'trailer', 'gate'];

/* ================= state ================= */
let S = null;               // last /api/office snapshot
let live = true;            // animate incoming events
const busy = new Set();     // person ids with a local animation in flight (sync leaves them alone)
const person = (id) => S.people.find(p => p.id === id);
const job = (id) => S.jobs.find(j => j.id === id);
const project = (id) => S.projects.find(p => p.id === id);
const jobsOf = (pid) => S.jobs.filter(j => j.assignee_id === pid);
const isMe = (p) => p && S && p.id === S.me.id;
const shown = (p) => isMe(p) ? 'You' : p.name;
const first = (p) => isMe(p) ? 'you' : (p.name.split(' ')[0].includes('.') ? p.name : p.name.split(' ')[0]);
const pmPerson = () => S.people.find(p => p.role === 'pm') || S.people.find(p => p.role === 'owner');
const waiting = () => S.inbox.filter(i => i.state === 'submitted');
function normLook(l = {}) { return { skin: l.skin || '#E8B98F', hair: l.hair || 'short', hairColor: l.hair_color || l.hairColor || '#2B211C', shirt: l.shirt || '#3F5A7A', glasses: !!l.glasses, beard: !!l.beard, vest: l.vest || null, hat: l.hat || null, prop: l.prop || 'mug' }; }
function adapt(snap) { snap.people.forEach(p => { p.look = normLook(p.look); }); return snap; }

/* ================= progress bits ================= */
const TONE = { ok: 'check', pend: 'clock', warn: 'alert', bad: 'flag', none: 'clock' };
const pill = (tone, text, icon) => `<span class="pill pill--${tone}">${ic(icon || TONE[tone] || 'clock')}${esc(text)}</span>`;
function bar(a, b) {
  return `<div class="bar" role="img" aria-label="${a}% approved${b > a ? `, ${b}% when approved` : ''}"><span class="bar__ok" style="width:${a}%"></span><span class="bar__pend" style="left:${a}%;width:${b - a}%"></span></div>
  <div class="bar__lbl"><b class="mono">${a}%</b>${b > a ? ` · <span class="mono">${b}%</span> when approved` : ' approved'}</div>`;
}

/* ================= illustration: people ================= */
function hairBack(L) { return L.hair === 'long' ? `<path d="M43 42Q42 22 60 22Q78 22 77 42L79 72Q60 78 41 72Z" fill="${L.hairColor}"/>` : ''; }
function hairFront(L) {
  const c = L.hairColor;
  if (L.hat) return '';
  switch (L.hair) {
    case 'short': return `<path d="M44.5 38Q44 22 60 22.5Q76 22 75.5 38Q74 30 67 29Q59 32.5 49 30Q46 33 44.5 38Z" fill="${c}"/>`;
    case 'bun': return `<circle cx="60" cy="20" r="6.5" fill="${c}"/><path d="M44.5 39Q44 23 60 23Q76 23 75.5 39Q71 29 60 29.5Q49 29 44.5 39Z" fill="${c}"/>`;
    case 'long': return `<path d="M44 44Q43 23 60 23Q77 23 76 44Q74 32 66 30Q57 34 46 32Q45 37 44 44Z" fill="${c}"/>`;
    case 'curly': return `<g fill="${c}"><circle cx="48" cy="31" r="6"/><circle cx="55" cy="26" r="6.5"/><circle cx="64" cy="25.5" r="6.5"/><circle cx="72" cy="30" r="6"/><circle cx="45.5" cy="37" r="4"/><circle cx="74.5" cy="37" r="4"/></g>`;
    case 'buzz': return `<path d="M45.5 35Q46.5 24 60 24Q73.5 24 74.5 35Q68 29.5 60 29.5Q52 29.5 45.5 35Z" fill="${c}" opacity=".9"/>`;
    case 'gray': return `<path d="M44.6 41Q44 31 48.5 27.5Q47 34 48.5 38ZM75.4 41Q76 31 71.5 27.5Q73 34 71.5 38Z" fill="${c}"/><path d="M49 27Q60 21 71 27Q60 25 49 27Z" fill="${c}"/>`;
  }
  return '';
}
function headFront(L) {
  let s = `<rect x="55" y="50" width="10" height="13" rx="4" fill="${L.skin}"/>
    <circle cx="45.5" cy="41" r="3.2" fill="${L.skin}"/><circle cx="74.5" cy="41" r="3.2" fill="${L.skin}"/>
    <circle cx="60" cy="39" r="15.5" fill="${L.skin}"/>`;
  if (L.beard) s += `<path d="M45.5 42Q46 56.5 60 57Q74 56.5 74.5 42Q71 50.5 60 51Q49 50.5 45.5 42Z" fill="${L.hairColor}"/>`;
  s += hairFront(L);
  s += `<g class="eyes"><ellipse cx="54.5" cy="41" rx="1.8" ry="2.1" fill="#14213D"/><ellipse cx="65.5" cy="41" rx="1.8" ry="2.1" fill="#14213D"/></g>
    <circle cx="50.5" cy="46.2" r="2.6" fill="#E26D5A" opacity=".22"/><circle cx="69.5" cy="46.2" r="2.6" fill="#E26D5A" opacity=".22"/>
    <path d="M56.5 47.6Q60 50.8 63.5 47.6" stroke="#14213D" stroke-width="1.5" fill="none" stroke-linecap="round"/>`;
  if (L.glasses) s += `<g fill="none" stroke="#14213D" stroke-width="1.3"><circle cx="54.5" cy="41" r="4.2"/><circle cx="65.5" cy="41" r="4.2"/><path d="M58.7 41h2.6"/></g>`;
  if (L.hat) s += `<path d="M44 36Q44 20 60 20Q76 20 76 36Z" fill="${L.hat}" stroke="#14213D" stroke-width="1.2"/><path d="M60 20.6V35" stroke="#000" stroke-opacity=".14" stroke-width="3.2"/><rect x="40.5" y="34" width="39" height="4.6" rx="2.3" fill="${L.hat}" stroke="#14213D" stroke-width="1.2"/>`;
  s += `<g class="headset"><rect x="71.5" y="37" width="6.5" height="9.5" rx="3.2" fill="#F2C230" stroke="#14213D" stroke-width="1.1"/><path d="M74.5 46.5Q74 52 67 52.5" stroke="#14213D" stroke-width="1.6" fill="none" stroke-linecap="round"/><circle class="led" cx="66" cy="52.5" r="1.9" fill="#F2C230" stroke="#14213D" stroke-width=".9"/></g>`;
  return s;
}
function torso(L) {
  let s = `<path d="M33 104L35.5 78Q37.5 64.5 60 62.5Q82.5 64.5 84.5 78L87 104Z" fill="${L.shirt}"/>`;
  if (L.vest) s += `<path d="M36.5 104L38 79Q39.5 68 51.5 64.5L54.5 104ZM83.5 104L82 79Q80.5 68 68.5 64.5L65.5 104Z" fill="${L.vest}"/><rect x="37.6" y="84" width="16" height="3.2" fill="#EEF2F5"/><rect x="66.4" y="84" width="16" height="3.2" fill="#EEF2F5"/>`;
  else s += `<path d="M53.5 63L60 71L66.5 63Q60 61.5 53.5 63Z" fill="#FFFFFF" opacity=".9"/>`;
  return s;
}
function propSVG(kind) {
  switch (kind) {
    case 'radio': return `<rect x="93" y="81" width="8" height="15" rx="2" fill="#2B303B"/><rect x="95" y="84" width="4" height="4" rx="1" fill="#62C08A"/><rect x="95.5" y="74" width="2" height="8" rx="1" fill="#2B303B"/>`;
    case 'tube': return `<rect x="84" y="90" width="28" height="6" rx="3" fill="#DCE6F2" stroke="#2D5C9B" stroke-width="1"/><ellipse cx="112" cy="93" rx="1.6" ry="3" fill="#2D5C9B"/>`;
    case 'binder': return `<rect x="90" y="79" width="7" height="17" rx="1" fill="#B42318"/><rect x="97.5" y="81" width="7" height="15" rx="1" fill="#3B5B8C"/><rect x="105" y="83" width="6" height="13" rx="1" fill="#2F7D4F"/>`;
    default: return `<rect x="92" y="85" width="9.5" height="11" rx="2" fill="#FFFFFF" stroke="#14213D" stroke-width="1.1"/><path d="M101.5 87.5q4.5 1.2 0 5.6" stroke="#14213D" stroke-width="1.1" fill="none"/><path class="steam" d="M96.5 82q-2.2-3 0-6t0-6" stroke="#9AA3B2" stroke-width="1.3" fill="none" stroke-linecap="round"/>`;
  }
}
const deskSVG = `<rect x="5" y="96" width="110" height="7" rx="2" class="d-top"/><rect x="9" y="103" width="102" height="13" class="d-front"/><rect x="13" y="90" width="22" height="6" rx="1" fill="#FFFFFF" stroke="#C9C2B4"/><rect x="15" y="87" width="20" height="4" rx="1" fill="#F7F3EA" stroke="#C9C2B4"/>`;
function seatedSVG(p) {
  const L = p.look;
  return `<svg class="fig" viewBox="0 0 120 116" aria-hidden="true" focusable="false">
    <rect x="32" y="44" width="56" height="58" rx="14" class="chair"/>
    <g class="bod">${hairBack(L)}${torso(L)}${headFront(L)}</g>
    <path d="M38.5 77Q33.5 89 42 95" stroke="${L.shirt}" stroke-width="9" stroke-linecap="round" fill="none"/>
    <path d="M81.5 77Q86.5 89 78 95" stroke="${L.shirt}" stroke-width="9" stroke-linecap="round" fill="none"/>
    <path d="M47 74h26a2 2 0 0 1 2 2v20H45V76a2 2 0 0 1 2-2z" fill="#C9D1DC" stroke="#14213D" stroke-width="1.2"/>
    <circle class="sticker" cx="60" cy="85" r="3.6" fill="#F2C230" stroke="#14213D" stroke-width=".8"/>
    <circle class="hand-l" cx="43" cy="95" r="4.3" fill="${L.skin}"/><circle class="hand-r" cx="77" cy="95" r="4.3" fill="${L.skin}"/>
    ${deskSVG}${propSVG(L.prop)}
  </svg>`;
}
const openDeskSVG = `<svg class="fig" viewBox="0 0 120 116" aria-hidden="true"><rect x="32" y="44" width="56" height="58" rx="14" class="chair"/><circle cx="60" cy="70" r="11" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-dasharray="3 3" opacity=".7"/><path d="M60 64v12M54 70h12" stroke="#FFFFFF" stroke-width="2" opacity=".8" stroke-linecap="round"/>${deskSVG}</svg>`;
function walkerSVG(L) {
  const vest = L.vest ? `<path d="M18.5 80L19.5 53Q21 45 27 43L28 80ZM41.5 80L40.5 53Q39 45 33 43L32 80Z" fill="${L.vest}"/><rect x="19" y="62" width="9" height="2.6" fill="#EEF2F5"/><rect x="32" y="62" width="9" height="2.6" fill="#EEF2F5"/>` : '';
  return `<svg viewBox="0 0 60 124" aria-hidden="true">
    <g class="w-leg w-leg-l"><rect x="21" y="74" width="8.5" height="36" rx="3.5" fill="#3A4357"/><rect x="18" y="106" width="12.5" height="6.5" rx="3" fill="#1B2130"/></g>
    <g class="w-leg w-leg-r"><rect x="30.5" y="74" width="8.5" height="36" rx="3.5" fill="#3A4357"/><rect x="29.5" y="106" width="12.5" height="6.5" rx="3" fill="#1B2130"/></g>
    <g class="w-arm w-arm-l"><path d="M18 47Q14 58 15 70" stroke="${L.shirt}" stroke-width="7" stroke-linecap="round" fill="none"/><circle cx="15" cy="72" r="3.6" fill="${L.skin}"/></g>
    <g transform="translate(-30 -20)">${hairBack(L)}</g>
    <path d="M16.5 80L18 52Q20 42 30 41Q40 42 42 52L43.5 80Z" fill="${L.shirt}"/>${vest}
    <g class="w-arm w-arm-r"><path d="M42 47Q46 58 45 70" stroke="${L.shirt}" stroke-width="7" stroke-linecap="round" fill="none"/><circle cx="45" cy="72" r="3.6" fill="${L.skin}"/></g>
    <g transform="translate(-30 -20)">${headFront(L)}</g>
  </svg>`;
}
function avatar(p, cls = '') { const L = p.look; return `<span class="av ${cls}" aria-hidden="true"><svg viewBox="37 13 46 46">${hairBack(L)}${torso(L)}${headFront(L)}</svg></span>`; }

/* ================= workstations ================= */
const KIND_ICON = { idle: 'chat', work: 'pen', pend: 'clock', ok: 'check', warn: 'alert', bad: 'flag', offline: 'wifi-off', input: 'image' };
function bubbleInner(b) { return `${ic(b.icon || KIND_ICON[b.kind] || 'chat')}<span class="bubble__t">${esc(b.text)}</span>`; }
function tagsHTML(p) {
  const js = jobsOf(p.id);
  const color = (j) => (project(j.project_id) || {}).color || '#5B6475';
  return js.slice(0, 2).map(j => `<span class="tag" style="--c:${color(j)}">${esc(j.tag)}</span>`).join('') + (js.length > 2 ? `<span class="tag" style="--c:var(--slate)">+${js.length - 2}</span>` : '');
}
function wsLabel(p) { return `${shown(p)}, ${p.role_label}. Assistant: ${p.bubble.text}. Open details.`; }
function wsEl(p) {
  const b = make('button', 'ws');
  b.id = 'ws-' + p.id; b.dataset.person = p.id;
  b.style.setProperty('--d', (-(p.id * 7 % 11) * .45) + 's');
  b.setAttribute('aria-label', wsLabel(p));
  b.innerHTML = `<span class="bubble" data-kind="${esc(p.bubble.kind)}">${bubbleInner(p.bubble)}</span>
    ${seatedSVG(p)}
    <span class="plate"><span class="plate__name">${esc(shown(p))}</span><span class="plate__role">${esc(p.company || p.role_label)}</span></span>
    <span class="tags">${tagsHTML(p)}</span>`;
  b.classList.toggle('is-working', p.bubble.kind === 'work');
  b.classList.toggle('is-offline', p.link === 'offline');
  return b;
}
function openDeskEl(room) {
  const b = make('button', 'ws ws--open');
  b.dataset.act = 'hire'; b.dataset.room = room;
  b.setAttribute('aria-label', `Open desk in ${ROOM_LABEL[room]}. Add a person.`);
  b.innerHTML = `<span class="bubble" data-kind="offline">${ic('plus')}<span class="bubble__t">Open desk</span></span>${openDeskSVG}<span class="plate"><span class="plate__name">Open desk</span><span class="plate__role">Add someone</span></span><span class="tags"></span>`;
  return b;
}
function roomCounts() { ['design', 'trailer'].forEach(r => { $('#room-' + r).dataset.n = $$('#desks-' + r + ' .ws').length; }); }
/* Bring the desks in line with S.people without disturbing animations in flight. */
function syncDesks() {
  const ids = new Set(S.people.map(p => p.id));
  $$('.ws[data-person]').forEach(ws => {
    const id = +ws.dataset.person;
    if (busy.has(id)) return;
    if (!ids.has(id)) ws.replaceWith(openDeskEl(ws.closest('.room').id.replace('room-', '')));
  });
  S.people.forEach(p => {
    if (busy.has(p.id)) return;
    const ws = $('#ws-' + p.id);
    const container = $('#desks-' + p.room);
    if (!ws) {
      const el = wsEl(p);
      const open = $(`#room-${p.room} .ws--open`);
      if (open) open.replaceWith(el); else container.appendChild(el);
      return;
    }
    const nw = wsEl(p);
    if (ws.closest('.room').id !== 'room-' + p.room) { ws.remove(); container.appendChild(nw); }
    else if (ws.innerHTML !== nw.innerHTML || ws.className !== nw.className) {
      $('.plate', ws).outerHTML = $('.plate', nw).outerHTML;
      $('.tags', ws).innerHTML = tagsHTML(p);
      if ($('.fig', ws).outerHTML !== $('.fig', nw).outerHTML) $('.fig', ws).outerHTML = $('.fig', nw).outerHTML;
      setBubbleEl(ws, p.bubble, false);
      ws.classList.toggle('is-offline', p.link === 'offline');
      ws.setAttribute('aria-label', wsLabel(p));
    }
  });
  roomCounts();
}
function setBubbleEl(ws, b, popIt = true) {
  const el = $('.bubble', ws);
  if (el.dataset.kind === b.kind && el.textContent.trim() === (b.text || '').trim()) return;
  el.dataset.kind = b.kind; el.innerHTML = bubbleInner(b);
  if (popIt) { el.classList.remove('pop'); void el.offsetWidth; el.classList.add('pop'); }
  ws.classList.toggle('is-working', b.kind === 'work');
}
function setBubble(pid, kind, text, icon) {
  const p = S && person(pid); if (!p) return;
  p.bubble = { kind, text, icon };
  const ws = $('#ws-' + pid); if (!ws) return;
  setBubbleEl(ws, p.bubble, true);
  ws.setAttribute('aria-label', wsLabel(p));
}

/* ================= props, header, rail ================= */
function renderIntray() {
  const n = waiting().length;
  $('#intrayCount').textContent = n;
  $('#intray').setAttribute('aria-label', `In-tray, ${n} report${n === 1 ? '' : 's'} waiting. Open.`);
  let s = '';
  for (let i = 0; i < Math.min(n, 6); i++) s += `<g transform="translate(${(i % 2) * 2} ${-i * 4.6})"><path d="M16 34h12l2 2h32v6H16z" fill="${i % 2 ? '#E2B65A' : '#EBC873'}" stroke="#14213D" stroke-width=".9"/></g>`;
  $('#intrayStack').innerHTML = s;
  $('#wbLine1').textContent = n ? `In-tray · ${n} waiting` : 'In-tray clear';
  $('#wbLine2').textContent = waiting().some(i => i.group === 'flagged') ? 'Flagged report to check' : '';
}
function sheetHTML(pr, k) {
  const badge = pr.plan_status === 'review' ? pill('warn', 'Plan to review', 'clock') : pr.plan_status === 'approved' ? pill('ok', 'Plan locked', 'lock') : pr.plan_status === 'reading' ? pill('pend', 'Reading', 'clock') : pill('none', 'No plan yet');
  return `<button class="sheet" id="sheet-${pr.id}" data-act="open-project" data-proj="${pr.id}" style="--c:${pr.color};--rot:${k % 2 ? '1.2deg' : '-1.5deg'}" aria-label="${esc(pr.name)}, ${pr.pct[0]}% approved. Open project.">
    <span class="sheet__name">${esc(pr.short)}</span><span class="sheet__bar"><i style="width:${pr.pct[0]}%"></i></span><span class="sheet__pct mono">${pr.pct[0]}% approved</span>${badge}</button>`;
}
function renderBoard() { $('#sheets').innerHTML = S.projects.map(sheetHTML).join('') + `<button class="sheet sheet--add" data-act="new-project">${ic('plus')}<span>New project</span></button>`; }
function renderNeeds() {
  const out = [];
  const w = waiting();
  if (w.length) out.push({ tone: 'pend', icon: 'inbox', text: `${w.length} report${w.length === 1 ? '' : 's'} in the in-tray`, act: 'open-intray', btn: 'Open' });
  w.filter(i => i.group === 'flagged').forEach(i => out.push({ tone: 'bad', icon: 'flag', text: `${(job(i.job_id) || {}).tag}: ${i.verdict}`, act: 'open-intray', btn: 'Review' }));
  S.projects.filter(p => p.plan_status === 'review').forEach(p => out.push({ tone: 'warn', icon: 'clock', text: `${p.short} plan is waiting for your approval`, act: 'plan-review', data: `data-proj="${p.id}"`, btn: 'Review' }));
  S.jobs.filter(j => !j.assignee_id).forEach(j => out.push({ tone: 'warn', icon: 'alert', text: `${j.name} needs someone`, act: 'assign-job', data: `data-job="${j.id}"`, btn: 'Assign' }));
  S.people.filter(p => p.link === 'offline').forEach(p => out.push({ tone: 'none', icon: 'wifi-off', text: `${p.name} is offline · ${p.queued} items queued. They’ll upload automatically.`, act: 'open-person', data: `data-id="${p.id}"`, btn: 'View' }));
  $('#needs').innerHTML = out.length ? out.map(n => `<li class="need"><span class="need__ic pill--${n.tone}">${ic(n.icon)}</span><span class="need__t">${esc(n.text)}</span><button class="btn btn--ghost" data-act="${n.act}" ${n.data || ''}>${n.btn}</button></li>`).join('') : `<li class="need--empty">${ic('check')} Nothing needs you right now.</li>`;
}
function renderProjects() {
  $('#projlist').innerHTML = S.projects.map(pr => `<button class="proj" style="--c:${pr.color}" data-act="open-project" data-proj="${pr.id}"><span class="proj__name">${esc(pr.name)}</span>${bar(pr.pct[0], pr.pct[1])}</button>`).join('') || '<p class="empty">No projects yet. Start one with New project.</p>';
}
function evKind(c) { return c.kind === 'photo' ? 'photo' : c.kind === 'voice' ? 'voice' : 'doc'; }
function evChip(c) { if (!c) return ''; const k = evKind(c); return `<button class="ev" data-act="evidence" data-blob="${c.blob_id || ''}" data-kind="${k}" data-label="${esc(c.label)}"><span class="ev__thumb ev__thumb--${k}">${ic(k === 'photo' ? 'image' : k === 'voice' ? 'mic' : 'file')}</span><span>${esc(c.label)}</span></button>`; }
function renderLog(fresh) {
  $('#log').innerHTML = S.log.slice(0, 40).map((e, i) => `<li class="${fresh && i === 0 ? 'new' : ''}"><time>${esc(fmtWhen(e.at))}</time><div><span>${esc(e.text)}</span>${e.chip && e.chip.blob_id ? `<div class="chips">${evChip(e.chip)}</div>` : ''}</div></li>`).join('') || '<li><time></time><div class="empty">Nothing yet today.</div></li>';
}
function renderHeader() {
  $('#orgName').textContent = S.org.name;
  $('#meRole').textContent = `${S.me.name} · ${ROLES[S.me.role].label}`;
  $('#stageSub').textContent = `${new Date().toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })} · ${S.people.length} desks · ${S.projects.length} project${S.projects.length === 1 ? '' : 's'}`;
  const roof = $('.roof__sign'); if (roof) roof.lastChild.textContent = ' ' + S.org.name;
  const site = S.projects.find(p => p.plan_status === 'approved') || S.projects[0];
  $('#siteName').textContent = site ? `${site.short} job site` : 'Job site';
  $('#assistantMode').innerHTML = `<svg class="ic" aria-hidden="true"><use href="#i-asst"/></svg>${S.assistant === 'claude' ? 'Assistant · Claude' : 'Assistant · rules engine'}`;
}
function renderRail() { renderNeeds(); renderProjects(); renderIntray(); renderBoard(); renderHeader(); }
function updateClocks() {
  const d = new Date(), h = d.getHours() % 12, m = d.getMinutes();
  $$('.clock').forEach(c => { $('.hh', c).style.transform = `rotate(${h * 30 + m * .5}deg)`; $('.mh', c).style.transform = `rotate(${m * 6}deg)`; });
  $('#simClock').textContent = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  $('#todayDate').textContent = d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

/* ================= toasts, drawer, modal, confirm, stamp ================= */
function toast(msg, icon = 'check') {
  const el = make('div', 'toast', `${ic(icon)}<span>${esc(msg)}</span>`);
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 320); }, 3800);
}
let drawerKey = null, drawerOpener = null;
function openDrawer({ key, lead = '', eyebrow = '', title, sub = '', body, foot = '' }) {
  const d = $('#drawer'), sc = $('#scrim');
  if (!drawerKey) drawerOpener = document.activeElement;
  drawerKey = key;
  $('#drawerLead').innerHTML = lead; $('#drawerEyebrow').textContent = eyebrow;
  $('#drawerTitle').textContent = title; $('#drawerSub').textContent = sub;
  $('#drawerBody').innerHTML = body; $('#drawerFoot').innerHTML = foot;
  $$('.confirm,.stampfx', d).forEach(x => x.remove());
  if (d.hidden) { d.hidden = false; sc.hidden = false; void d.offsetWidth; d.classList.add('on'); sc.classList.add('on'); setTimeout(() => $('[data-act="close-drawer"]', d).focus(), 60); }
}
function closeDrawer() {
  const d = $('#drawer'), sc = $('#scrim'); if (d.hidden) return;
  d.classList.remove('on'); sc.classList.remove('on'); drawerKey = null;
  setTimeout(() => { if (!d.classList.contains('on')) { d.hidden = true; if ($('#modal').hidden) sc.hidden = true; } }, 320);
  if (drawerOpener && document.contains(drawerOpener)) drawerOpener.focus({ preventScroll: true });
}
function openModal({ eyebrow = '', title, sub = '', body, foot = '' }) {
  const m = $('#modal'), sc = $('#scrim');
  $('#modalEyebrow').textContent = eyebrow; $('#modalTitle').textContent = title; $('#modalSub').textContent = sub;
  $('#modalBody').innerHTML = body; $('#modalFoot').innerHTML = foot;
  if (m.hidden) { m.hidden = false; sc.hidden = false; void sc.offsetWidth; sc.classList.add('on'); setTimeout(() => { const f = $('#modalBody input, #modalBody button', m); (f || $('[data-act="close-modal"]', m)).focus(); }, 60); }
}
function closeModal() {
  const m = $('#modal'), sc = $('#scrim'); if (m.hidden) return;
  m.hidden = true;
  if ($('#drawer').hidden) { sc.classList.remove('on'); setTimeout(() => { if ($('#modal').hidden && $('#drawer').hidden) sc.hidden = true; }, 260); }
}
function showConfirm({ title, body, okLabel, okClass = 'btn--primary', icon, onOk }) {
  const host = $('#drawer');
  $$('.confirm', host).forEach(x => x.remove());
  const el = make('div', 'confirm', `<div class="confirm__card" role="alertdialog" aria-modal="true" aria-labelledby="cfT"><h3 id="cfT">${icon ? ic(icon) : ''}${esc(title)}</h3>${body}<div class="confirm__acts"><button class="btn btn--ghost" data-c="no">Cancel</button><button class="btn btn--lg ${okClass}" data-c="ok">${icon ? ic(icon) : ''}${esc(okLabel)}</button></div></div>`);
  host.appendChild(el);
  setTimeout(() => $('[data-c="no"]', el).focus(), 40);
  el.addEventListener('click', e => {
    const b = e.target.closest('[data-c]');
    if (!b) { if (e.target === el) el.remove(); return; }
    e.stopPropagation(); el.remove();
    if (b.dataset.c === 'ok') onOk();
  });
}
async function stamp(text) {
  const host = $('#drawer');
  const el = make('div', 'stampfx', `<svg class="stampfx__tool" viewBox="0 0 76 110" aria-hidden="true"><rect x="28" y="2" width="20" height="44" rx="10" fill="#8B6B47" stroke="#14213D" stroke-width="2"/><rect x="18" y="44" width="40" height="12" rx="3" fill="#5B6475" stroke="#14213D" stroke-width="2"/><rect x="8" y="56" width="60" height="22" rx="4" fill="#2F7D4F" stroke="#14213D" stroke-width="2"/></svg><div class="stampfx__ink">${ic('lock')}<span>${esc(text)}</span></div>`);
  host.appendChild(el);
  await sleep(1350); el.classList.add('out'); await sleep(320); el.remove();
}

/* ================= motion ================= */
function spawnWalker(p, x, y) {
  fxSize();
  const el = make('div', 'walker', walkerSVG(p.look));
  el.style.left = (x - 23) + 'px'; el.style.top = (y - 86) + 'px';
  fx.appendChild(el); return el;
}
async function walkTo(el, x, y, speed = 150) {
  const cx = parseFloat(el.style.left) + 23, cy = parseFloat(el.style.top) + 86;
  const dx = x - cx, dy = y - cy; const dist = Math.hypot(dx, dy);
  if (dist < 2) return;
  el.classList.add('walking'); el.classList.toggle('flip', dx < 0);
  const a = el.animate([{ transform: 'translate(0,0)' }, { transform: `translate(${dx}px,${dy}px)` }], { duration: Math.max(380, dist / speed * 1000), easing: 'linear', fill: 'forwards' });
  await a.finished;
  el.style.left = (x - 23) + 'px'; el.style.top = (y - 86) + 'px'; a.cancel(); el.classList.remove('walking');
}
async function fadeEl(el, inOut) {
  const k = inOut === 'in' ? [{ opacity: 0, transform: 'scale(.9)' }, { opacity: 1, transform: 'none' }] : [{ opacity: 1 }, { opacity: 0, transform: 'scale(.92)' }];
  await el.animate(k, { duration: 260, fill: 'forwards' }).finished;
}
function nearView(el) { if (!el) return; const r = el.getBoundingClientRect(); if (r.top < 70 || r.bottom > innerHeight - 10) el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' }); }
const INPUT_ICON = { photo: 'image', voice: 'mic', doc: 'file', text: 'chat' };
async function dropPacket(pid, kind, label) {
  const ws = $('#ws-' + pid); if (!ws) return;
  const ant = $('#antenna'); ant.classList.remove('ping'); void ant.offsetWidth; ant.classList.add('ping');
  if (reduce || !live) return;
  fxSize();
  const p = person(pid);
  const r = docRect($('.fig', ws));
  const k = kind === 'photo' ? 'photo' : kind === 'voice' ? 'voice' : 'doc';
  const el = make('div', 'packet', `<span class="ev__thumb ev__thumb--${k}">${ic(INPUT_ICON[kind] || 'file')}</span><span>${esc(label)}${p ? `<small>${ic('phone')}from ${esc(p.name)}’s ${p.device === 'laptop' ? 'laptop' : 'phone'}</small>` : ''}</span>`);
  fx.appendChild(el);
  el.style.left = (r.cx - el.offsetWidth / 2) + 'px'; el.style.top = (r.top - 74) + 'px';
  const dy = r.top + r.height * .74 - (r.top - 74);
  await el.animate([{ transform: 'translateY(-24px) scale(.6)', opacity: 0 }, { transform: 'translateY(0) scale(1)', opacity: 1, offset: .25 }, { transform: 'translateY(0) scale(1)', opacity: 1, offset: .55 }, { transform: `translateY(${dy}px) scale(.35)`, opacity: .2 }], { duration: 1500, easing: 'ease-in' }).finished;
  el.remove();
  ws.classList.add('is-rx'); setTimeout(() => ws.classList.remove('is-rx'), 1300);
}
async function flyFolder(fromEl, toEl, label) {
  if (!fromEl || !toEl || reduce || !live) return;
  fxSize();
  const a = docRect(fromEl), b = docRect(toEl);
  const el = make('div', 'flyer', `<svg class="folder" viewBox="0 0 38 30" aria-hidden="true"><path d="M2 6a2 2 0 0 1 2-2h10l3 3h17a2 2 0 0 1 2 2v17a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z" fill="#EBC873" stroke="#14213D" stroke-width="1.5"/><path d="M2 11h34" stroke="#14213D" stroke-width="1" opacity=".4"/></svg><span>${esc(label)}</span>`);
  fx.appendChild(el);
  el.style.left = (a.cx - 19) + 'px'; el.style.top = (a.cy - 15) + 'px';
  const dx = b.cx - a.cx, dy = b.cy - a.cy, lift = Math.min(-60, -Math.abs(dx) * .25);
  await el.animate([{ transform: 'translate(0,0) scale(.6)', opacity: 0 }, { transform: `translate(${dx * .15}px,${dy * .15 + lift * .6}px) scale(1)`, opacity: 1, offset: .15 }, { transform: `translate(${dx * .55}px,${dy * .55 + lift}px) scale(1)`, opacity: 1, offset: .55 }, { transform: `translate(${dx}px,${dy}px) scale(.5)`, opacity: .3 }], { duration: 1500, easing: 'ease-in-out' }).finished;
  el.remove();
}
async function flyTag(fromEl, toWs, text, color) {
  if (!fromEl || !toWs || reduce || !live) return;
  fxSize();
  const a = docRect(fromEl), b = docRect($('.tags', toWs) || toWs);
  const el = make('div', 'flytag', `<span class="tag" style="--c:${color}">${esc(text)}</span>`);
  fx.appendChild(el);
  el.style.left = (a.cx - 30) + 'px'; el.style.top = (a.cy - 10) + 'px';
  const dx = b.cx - a.cx, dy = b.cy - a.cy;
  await el.animate([{ transform: 'translate(0,0) scale(1.3)', opacity: 0 }, { transform: `translate(${dx * .5}px,${dy * .5 - 70}px) scale(1.2)`, opacity: 1, offset: .5 }, { transform: `translate(${dx}px,${dy}px) scale(1)`, opacity: 1 }], { duration: 1000, easing: 'ease-in-out' }).finished;
  el.remove();
}
/* A person walks from one room to another (role change, hire, removal). */
async function walkBetween(p, from, toWs, toRoom) {
  if (reduce) return;
  if (from) {
    const w = spawnWalker(p, from.x, from.y);
    if (from.wave) { w.classList.add('waving'); await sleep(1400); w.classList.remove('waving'); }
    await walkTo(w, from.exitX, from.y); await fadeEl(w, 'out'); w.remove();
  }
  if (toWs) {
    const room = $('#room-' + toRoom); nearView(room); await sleep(450);
    const rr = docRect(room), wr = docRect($('.fig', toWs)), ty = wr.bottom + 6;
    const fromRight = toRoom === 'trailer';
    const w2 = spawnWalker(p, fromRight ? rr.right - 16 : rr.left + 16, ty); await fadeEl(w2, 'in');
    await walkTo(w2, wr.cx, ty); w2.remove();
  }
}

/* ================= data refresh + live events ================= */
let refreshTimer = null;
function scheduleRefresh(ms = 350) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, ms); }
async function refresh() {
  try { S = adapt(await api('GET', '/api/office')); } catch (e) { return; }
  syncDesks(); renderRail();
  const typing = document.activeElement && $('#drawer').contains(document.activeElement) && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
  if (!typing && !$('#drawer .confirm') && !$('#drawer .stampfx') && !$('#drawer .panel')) refreshDrawer();
}
function addLogEvent(ev) {
  if (!ev.text) return;
  const c = ev.data && ev.data.blob_id ? { label: ev.data.label || 'File', kind: ev.data.kind || 'doc', blob_id: ev.data.blob_id } : null;
  S.log.unshift({ id: ev.id, at: ev.at, text: ev.text, chip: c });
  S.log = S.log.slice(0, 60);
  renderLog(true);
}
const planWaiters = new Map(); // project id -> resolve, for the new-project animation
async function onEvent(ev) {
  if (!S) return;
  const d = ev.data || {};
  addLogEvent(ev);
  switch (ev.type) {
    case 'assistant.status':
      if (!busy.has(ev.person_id)) setBubble(ev.person_id, d.kind, d.label, d.icon);
      return;
    case 'input.received':
      dropPacket(ev.person_id, d.kind, d.label);
      scheduleRefresh(1600);
      return;
    case 'report.submitted': {
      const ws = $('#ws-' + ev.person_id);
      if (ws) nearView(ws);
      await flyFolder(ws, $('#intray'), d.change || 'Report');
      const t = $('#intray'); t.classList.remove('bump'); void t.offsetWidth; t.classList.add('bump');
      scheduleRefresh(50);
      return;
    }
    case 'plan.reading': {
      const cc = $('#cabCount');
      if (cc.hidden === false && d.pages) cc.dataset.total = d.pages;
      scheduleRefresh();
      return;
    }
    case 'plan.proposed': {
      const w = planWaiters.get(d.project_id);
      if (w) w();
      scheduleRefresh();
      return;
    }
    default:
      scheduleRefresh();
  }
}
function connect() {
  const es = new EventSource(`/api/events/stream?after=${S.last_event_id || 0}`);
  es.onmessage = (m) => { try { const ev = JSON.parse(m.data); S.last_event_id = ev.id; onEvent(ev); } catch (e) { console.error(e); } };
  es.onerror = () => { /* the browser reconnects with Last-Event-ID */ };
}

/* ================= person drawer ================= */
function linkPill(p) {
  if (isMe(p)) return pill('ok', 'You', 'check');
  if (p.link === 'offline') return pill('none', `Offline · ${p.queued} items queued`, 'wifi-off');
  if (p.status === 'invited') return pill('pend', 'Invite sent · not linked yet', 'clock');
  return pill('ok', `Linked · ${p.device}`, 'link');
}
function inputRow(x) {
  const k = x.kind === 'photo' ? 'photo' : x.kind === 'voice' ? 'voice' : 'doc';
  const chip = x.blob_id ? `data-act="evidence" data-blob="${x.blob_id}" data-kind="${k}" data-label="${esc(x.text || '')}"` : '';
  return `<li class="inp" ${chip} style="${x.blob_id ? 'cursor:pointer' : ''}"><span class="ev__thumb ev__thumb--${k}">${ic(INPUT_ICON[x.kind] || 'file')}</span><div>${esc(x.text)}${x.quote ? `<span class="q">“${esc(x.quote)}”</span>` : ''}</div><time>${esc(fmtWhen(x.at))}</time></li>`;
}
function jobCard(j, opts = {}) {
  const pr = project(j.project_id) || { color: '#5B6475', short: '' }; const w = j.assignee_id && person(j.assignee_id);
  return `<div class="job">
    <div class="job__top"><span class="job__name">${esc(j.name)}</span><span class="tag" style="--c:${pr.color}">${esc(pr.short)}</span></div>
    ${bar(j.approved_pct, j.pending_pct)}
    <div class="job__meta"><span class="mono">${esc(j.qty_label)}</span>${pill(j.status[0], j.status[1])}</div>
    ${opts.who ? `<div class="job__meta"><span>${w ? esc(shown(w)) + ' · ' + esc(w.role_label) : 'Nobody yet'}</span>${!w ? `<button class="btn btn--ghost" data-act="assign-job" data-job="${j.id}">Assign</button>` : ''}</div>` : ''}
    ${opts.unassign ? `<button class="linkbtn" data-act="unassign" data-job="${j.id}">Take off this job</button>` : ''}
  </div>`;
}
function roleCards(cur, name) {
  return ROLE_ORDER.map(r => { const R = ROLES[r]; return `<label class="role"><input type="radio" name="${name}" value="${r}" ${r === cur ? 'checked' : ''}><b>${R.label}</b><span><em>Assistant can:</em> ${esc(R.can)}</span><span><em>Needs approval:</em> ${esc(R.needs)}</span><span><em>Sits in:</em> ${ROOM_LABEL[R.room]}</span></label>`; }).join('');
}
function openPerson(id) {
  const p = person(id); if (!p) return;
  if (isMe(p)) return openAsk();
  const js = jobsOf(id);
  const approver = p.role === 'pm' ? S.people.find(x => x.role === 'owner') : pmPerson();
  const body = `
    <section class="sec">
      <div class="linkrow">${linkPill(p)}<span class="where">${ic('pin')}${esc(p.where || '')}</span></div>
      <div class="relay" aria-label="How work flows from ${esc(p.name)} to approval">
        <div class="relay__node">${avatar(p)}<b>${esc(p.name)}</b><small>${p.device === 'laptop' ? 'Laptop' : 'Field app on phone'}</small></div>
        <div class="relay__wire" aria-hidden="true"><i></i></div>
        <div class="relay__node">${asstBadge()}<b>Assistant</b><small>Turns it into reports</small></div>
        <div class="relay__wire" aria-hidden="true"><i></i></div>
        <div class="relay__node">${approver ? avatar(approver) : ''}<b>${esc(approver ? shown(approver) : 'Manager')}</b><small>Approves</small></div>
      </div>
      <p class="explain">This desk is ${esc(first(p))}’s Assistant, not ${esc(first(p))}. ${esc(first(p))} sends photos, voice notes and files from their ${esc(p.device)}; the Assistant asks follow-up questions, drafts the report and sends it for approval.</p>
    </section>
    <section class="sec">
      <h3 class="h3">${asstBadge()} Assistant right now</h3>
      <div class="now"><span class="bubble" data-kind="${esc(p.bubble.kind)}" style="margin:0">${bubbleInner(p.bubble)}</span></div>
      <h4 class="h4">Latest from ${esc(p.name)}</h4>
      <ul class="inputs" id="personInputs">${p.inputs.map(inputRow).join('') || `<li class="empty">Nothing yet. Their first photo or note shows up here.</li>`}</ul>
    </section>
    <section class="sec" id="jobsSec">
      <div class="sec__head"><h3 class="h3">Jobs</h3><span class="eyebrow">${js.length} assigned</span></div>
      ${js.map(j => jobCard(j, { unassign: true })).join('') || `<p class="empty">No jobs yet. Add one so ${esc(first(p))}’s Assistant knows what to track.</p>`}
      <div id="addJobArea"><button class="btn btn--primary btn--lg btn--block" data-act="add-job-open" data-id="${p.id}">${ic('plus')}Add a job</button></div>
    </section>
    <section class="sec">
      <h3 class="h3">Role</h3>
      <p class="explain">Changing the role changes what the Assistant can do and where the desk sits.</p>
      <fieldset class="roles" data-role-for="${p.id}"><legend class="vh">Role for ${esc(p.name)}</legend>${roleCards(p.role, 'role-' + p.id)}</fieldset>
    </section>
    <section class="sec">
      <h3 class="h3">Details</h3>
      <label class="field"><span>Name</span><input id="pName" value="${esc(p.name)}" autocomplete="off"></label>
      <label class="field"><span>Phone or email for the field app</span><input id="pContact" value="${esc(p.contact || '')}" autocomplete="off"><small>Send them their field link; whatever they send goes to their Assistant.</small></label>
      <div class="linkrow"><button class="btn btn--ghost" data-act="save-details" data-id="${p.id}">Save details</button><button class="btn btn--ghost" data-act="new-link" data-id="${p.id}">${ic('link')}New field link</button></div>
    </section>
    <section class="sec sec--danger"><button class="btn btn--danger" data-act="ask-remove" data-id="${p.id}">${ic('trash')}Remove from office</button></section>`;
  const foot = p.role === 'pm' ? `<button class="btn btn--primary btn--lg" data-act="open-intray">${ic('inbox')}Open in-tray</button>` : '';
  openDrawer({ key: 'person:' + id, lead: avatar(p, 'av--lg'), eyebrow: p.company ? `${p.company} · ${p.role_label}` : p.role_label, title: p.name, sub: ROOM_LABEL[p.room], body, foot });
  api('GET', `/api/people/${id}`).then(full => { const ul = $('#personInputs'); if (ul && drawerKey === 'person:' + id && full.inputs.length) ul.innerHTML = full.inputs.slice(0, 8).map(inputRow).join(''); }).catch(() => {});
}
function refreshDrawer() {
  if (!drawerKey) return;
  const [k, v] = drawerKey.split(':');
  const scroll = $('#drawerBody').scrollTop;
  if (k === 'person' && person(+v)) openPerson(+v);
  else if (k === 'intray') openIntray();
  else if (k === 'project') openProject(+v, true);
  else return;
  $('#drawerBody').scrollTop = scroll;
}

/* ----- add job ----- */
let AJ = null;
function addJobPanel() {
  const p = person(AJ.pid);
  const others = S.jobs.filter(j => j.assignee_id !== AJ.pid).sort((a, b) => (a.assignee_id ? 1 : 0) - (b.assignee_id ? 1 : 0));
  const existing = others.length ? `<div class="picklist" role="group" aria-label="Jobs">${others.map(j => { const w = j.assignee_id && person(j.assignee_id); return `<button class="pick" aria-pressed="${AJ.pick === j.id}" data-act="aj-pick" data-job="${j.id}"><span class="pick__dot" style="--c:${(project(j.project_id) || {}).color}"></span><span class="pick__t">${esc(j.name)}<small>${w ? `Now with ${esc(shown(w))} · moves to ${esc(first(p))}` : 'Nobody on it yet'}</small></span></button>`; }).join('')}</div>` : `<p class="empty">Every job is already with ${esc(first(p))}.</p>`;
  const nw = `<div class="grid2">
      <label class="field"><span>Project</span><select id="ajProj">${S.projects.map(pr => `<option value="${pr.id}">${esc(pr.short)}</option>`).join('')}</select></label>
      <label class="field"><span>Job name</span><input id="ajName" value="${esc(AJ.name || '')}" placeholder="Line B · 6-in water main"></label>
    </div>
    <div class="field"><span>Measured by</span><div class="seg" role="group" aria-label="Measured by">${[['qty', 'Quantity'], ['steps', 'Weighted steps'], ['done', 'Done or not']].map(([v, l]) => `<button type="button" aria-pressed="${AJ.method === v}" data-act="aj-method" data-v="${v}">${l}</button>`).join('')}</div></div>
    ${AJ.method === 'qty' ? `<div class="grid2"><label class="field"><span>Total</span><input id="ajTotal" inputmode="numeric" class="mono" value="${esc(AJ.total || '')}" placeholder="1,200"></label><label class="field"><span>Unit</span><select id="ajUnit">${['LF', 'EA', 'SY', 'CY', 'sheets'].map(u => `<option ${AJ.unit === u ? 'selected' : ''}>${u}</option>`).join('')}</select></label></div>` : ''}
    <p class="err" id="ajErr" hidden></p>`;
  return `<div class="panel" role="group" aria-label="Add a job">
    <div class="sec__head"><h4 class="h3">Add a job for ${esc(first(p))}</h4></div>
    <div class="seg" role="group" aria-label="Job source"><button type="button" aria-pressed="${AJ.mode === 'existing'}" data-act="aj-mode" data-v="existing">Pick a job</button><button type="button" aria-pressed="${AJ.mode === 'new'}" data-act="aj-mode" data-v="new">New job</button></div>
    ${AJ.mode === 'existing' ? existing : nw}
    <div class="panel__acts"><button class="btn btn--quiet" data-act="aj-cancel">Cancel</button><button class="btn btn--primary" data-act="aj-save" id="ajSave">${ic('plus')}Add job</button></div>
  </div>`;
}
function renderAJ() { const area = $('#addJobArea'); if (area) area.innerHTML = addJobPanel(); }
function keepAJFields() { if (!AJ || AJ.mode !== 'new') return; const n = $('#ajName'), t = $('#ajTotal'), u = $('#ajUnit'); if (n) AJ.name = n.value; if (t) AJ.total = t.value; if (u) AJ.unit = u.value; }
async function assignAnimated(pid, jid, fromEl) {
  const before = job(jid);
  await api('PATCH', `/api/jobs/${jid}`, { assignee_id: pid });
  const ws = pid && $('#ws-' + pid);
  if (ws && before) { nearView(ws); await flyTag(fromEl, ws, before.tag, (project(before.project_id) || {}).color || '#5B6475'); }
  await refresh();
  const t = ws && $$('.tag', $('#ws-' + pid)).find(x => before && x.textContent === before.tag); if (t) t.classList.add('pop');
}

/* ----- role change and removal ----- */
async function changeRole(pid, role) {
  const p = person(pid); if (!p || p.role === role) return;
  const oldWs = $('#ws-' + pid);
  busy.add(pid);
  try {
    const np = await api('PATCH', `/api/people/${pid}`, { role });
    np.look = normLook(np.look);
    toast(`${p.name} is now ${ROLES[role].label}.`);
    if (np.room === p.room || !oldWs) { busy.delete(pid); await refresh(); return; }
    const oldRoom = oldWs.closest('.room'); const fr = docRect($('.fig', oldWs)), rr = docRect(oldRoom);
    oldWs.remove();
    const ws = wsEl({ ...p, ...np }); ws.style.visibility = 'hidden'; $('#desks-' + np.room).appendChild(ws); roomCounts();
    await walkBetween(np, { x: fr.cx, y: fr.bottom + 6, exitX: rr.left + 16 }, ws, np.room);
    ws.style.visibility = ''; ws.classList.add('arrive');
  } catch (e) { fail(e); }
  busy.delete(pid);
  await refresh();
}
async function removePerson(pid) {
  const p = person(pid); if (!p) return;
  closeDrawer();
  busy.add(pid);
  try {
    await api('DELETE', `/api/people/${pid}`);
    const ws = $('#ws-' + pid);
    if (ws) {
      const room = ws.closest('.room'); const roomKey = room.id.replace('room-', '');
      nearView(room); await sleep(300);
      const fr = docRect($('.fig', ws)), rr = docRect(room);
      ws.replaceWith(openDeskEl(roomKey)); roomCounts();
      await walkBetween(p, { x: fr.cx, y: fr.bottom + 6, exitX: roomKey === 'trailer' ? rr.right - 16 : rr.left + 16, wave: true }, null);
    }
    toast(`${p.name} removed. Their approved history stays locked in the record.`, 'lock');
  } catch (e) { fail(e); }
  busy.delete(pid);
  await refresh();
}

/* ================= hire ================= */
let H = null;
function openHire(presetRoom) {
  const roleForRoom = { design: 'cad', qaqc: 'qaqc', pm: 'pm', trailer: 'inspector', gate: 'contractor' };
  H = { name: '', role: roleForRoom[presetRoom] || 'inspector', contact: '', company: '', jobs: new Set() };
  renderHire();
}
function renderHire() {
  openModal({
    eyebrow: 'Add a person', title: 'Who’s joining?', sub: 'They get their own Assistant, matched to their role.',
    body: `<label class="field"><span>Name</span><input id="hName" value="${esc(H.name)}" autocomplete="off" placeholder="Priya Raman"><span class="err" id="hErr" hidden>Add a name so the desk has a nameplate.</span></label>
      <fieldset class="roles"><legend>Role</legend>${roleCards(H.role, 'hRole')}</fieldset>
      <label class="field"><span>Their phone or email</span><input id="hContact" value="${esc(H.contact)}" autocomplete="off" placeholder="(555) 201-3344"><small>You’ll get a private field-app link to send them. Whatever they send goes to their Assistant.</small></label>
      <label class="field"><span>Company (contractors only)</span><input id="hCompany" value="${esc(H.company)}" autocomplete="off"></label>
      <fieldset class="field"><legend>Jobs to start with (optional)</legend><div class="picklist">${S.jobs.map(j => `<button type="button" class="pick" aria-pressed="${H.jobs.has(j.id)}" data-act="h-job" data-job="${j.id}"><span class="pick__dot" style="--c:${(project(j.project_id) || {}).color}"></span><span class="pick__t">${esc(j.name)}<small>${j.assignee_id && person(j.assignee_id) ? `Now with ${esc(shown(person(j.assignee_id)))}` : 'Nobody on it yet'}</small></span></button>`).join('')}</div></fieldset>`,
    foot: `<button class="btn btn--quiet" data-act="close-modal">Cancel</button><button class="btn btn--primary btn--lg" data-act="hire-go">${ic('user-plus')}Add to office</button>`,
  });
}
function readHire() { const n = $('#hName'), c = $('#hContact'), co = $('#hCompany'), r = $('input[name="hRole"]:checked'); if (n) H.name = n.value.trim(); if (c) H.contact = c.value.trim(); if (co) H.company = co.value.trim(); if (r) H.role = r.value; }
async function hire() {
  readHire();
  if (!H.name) { $('#hErr').hidden = false; $('#hName').focus(); return; }
  let res;
  try { res = await api('POST', '/api/people', { name: H.name, role: H.role, contact: H.contact || null, company: H.company || null, job_ids: [...H.jobs] }); } catch (e) { fail(e); return; }
  closeModal();
  const p = res.person; p.look = normLook(p.look);
  busy.add(p.id);
  S.people.push(p);
  const room = p.room, R = ROLES[p.role];
  const open = $(`#room-${room} .ws--open`);
  const ws = wsEl(p); ws.style.visibility = 'hidden';
  if (open) open.replaceWith(ws); else $('#desks-' + room).appendChild(ws);
  roomCounts();
  if (!reduce) {
    if (!R.field) {
      const lobby = $('#room-lobby'); nearView(lobby); await sleep(400);
      const door = $('#frontDoor'), elev = $('#elevator'), lr = docRect(lobby);
      door.classList.add('open'); await sleep(250);
      const w = spawnWalker(p, docRect(door).cx, lr.bottom - 10); await fadeEl(w, 'in');
      await walkTo(w, docRect(elev).cx, lr.bottom - 10);
      door.classList.remove('open'); elev.classList.add('open'); await sleep(500);
      await fadeEl(w, 'out'); w.remove(); elev.classList.remove('open'); await sleep(350);
      $('#elevNum').textContent = ['owner', 'pm'].includes(room) ? '2 ▲' : '1'; await sleep(600);
    } else if (p.role !== 'contractor') {
      const truck = $('#truck'); nearView(truck); await sleep(400);
      truck.classList.add('open'); await sleep(300);
      const tr = docRect(truck);
      const w = spawnWalker(p, tr.left + tr.width * .62, tr.bottom - 6); await fadeEl(w, 'in');
      truck.classList.remove('open');
      await walkTo(w, tr.left - 4, tr.bottom - 6); await fadeEl(w, 'out'); w.remove();
    }
    await walkBetween(p, null, ws, room);
    $('#elevNum').textContent = '1';
  }
  ws.style.visibility = ''; ws.classList.add('arrive');
  busy.delete(p.id);
  await refresh();
  openModal({
    eyebrow: 'Field app link', title: `${p.name}’s Assistant is ready`, sub: 'Send them this private link. It opens the field app on their phone or laptop.',
    body: `<div class="linkbox"><code id="fieldLink">${esc(res.field_link)}</code><div class="linkrow"><button class="btn btn--primary" data-act="copy-link">${ic('link')}Copy link</button>${H.contact ? `<span class="quietline">For ${esc(H.contact)}</span>` : ''}</div></div><p class="quietline">Anyone with the link can send to this Assistant, so share it only with ${esc(p.name)}. You can make a new one from their desk at any time; old links keep working until the person is removed.</p>`,
    foot: `<button class="btn btn--primary" data-act="close-modal">Done</button>`,
  });
}

/* ================= in-tray ================= */
function repCard(i) {
  const p = person(i.person_id) || { name: 'Former team member', look: normLook(), role_label: '' }; const j = job(i.job_id) || { name: '' };
  return `<article class="rep" id="rep-${i.id}">
    <header class="rep__h">${avatar(p)}<div class="rep__who"><b>${esc(p.name)}</b><span>${esc(j.name)}</span></div><span class="rep__chg mono">${esc(i.change)}</span></header>
    ${i.where ? `<div class="rep__where mono">${esc(i.where)}</div>` : ''}
    <p class="rep__verdict">${asstBadge()}<span>${esc(i.verdict)}</span></p>
    <div class="chips">${i.chips.map(evChip).join('')}</div>
    <details class="brief"><summary>Review brief</summary>
      <div class="brief__cols"><div><h5>Claimed</h5><ul>${i.claimed.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div><div><h5>Evidence shows</h5><ul>${i.shows.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div></div>
      ${i.flags.map(f => `<p class="brief__hist">${ic(f.tone === 'bad' ? 'flag' : 'alert')}<span>${esc(f.text)}</span></p>`).join('')}
      ${i.usual ? `<p class="brief__hist">${ic('clock')}<span>${esc(i.usual)}</span></p>` : ''}
      ${i.missing.length ? `<p class="brief__hist">${ic('alert')}<span><b>Missing:</b> ${esc(i.missing.join(', '))}</span></p>` : ''}
      <p class="quiet">${esc(i.note || 'Checked against the original files, not the summary.')}${i.checked_by === 'claude' ? ' Photos read by Claude.' : ''}</p>
    </details>
    ${i.asked.map(q => `<p class="brief__hist">${ic('chat')}<span>You asked: “${esc(q)}”</span></p>`).join('')}
    <div class="rep__inline" id="inl-${i.id}"></div>
    <div class="rep__acts"><button class="btn btn--quiet" data-act="req-changes" data-id="${i.id}">Request changes</button><button class="btn btn--ghost" data-act="ask-item" data-id="${i.id}">${ic('chat')}Ask</button><button class="btn btn--ok" data-act="approve" data-id="${i.id}">${ic('check')}Approve</button></div>
  </article>`;
}
function entryHTML(h, prid) {
  return `<div class="entry"><div class="entry__top">${ic('lock')}<span>Approved by ${esc(h.by)} · ${esc(fmtWhen(h.at))}</span><span class="hash">${esc(h.fingerprint)}</span></div>
    <div class="entry__text ${h.amendment ? 'struck' : ''}">${esc(h.text)}</div>
    ${h.amendment ? `<div class="amend"><div class="amend__by">Amended by ${esc(h.amendment.by)} · ${esc(fmtWhen(h.amendment.at))} · <span class="hash">${esc(h.amendment.fingerprint)}</span></div><div class="entry__text">${esc(h.amendment.text)}</div><div class="amend__by">Reason: ${esc(h.amendment.reason)}</div></div>` : ''}
    ${prid && h.id ? `<button class="linkbtn" data-act="amend" data-proj="${prid}" data-entry="${h.id}">Amend</button><div id="amend-${h.id}"></div>` : ''}</div>`;
}
function openIntray() {
  const w = waiting();
  const routine = w.filter(i => i.group === 'routine');
  let body = '';
  if (routine.length > 1) body += `<button class="btn btn--ghost btn--lg btn--block" data-act="approve-batch">${ic('check')}Approve ${routine.length} routine items</button>`;
  [['attention', 'Needs attention', 'warn'], ['flagged', 'Flagged', 'bad'], ['routine', 'Routine · evidence complete', 'ok']].forEach(([g, label, tone]) => {
    const items = w.filter(i => i.group === g); if (items.length) body += `<div class="grp">${pill(tone, label)}${items.map(repCard).join('')}</div>`;
  });
  if (!w.length) body += `<p class="empty">${ic('check')} The in-tray is clear. New reports land here when people send them.</p>`;
  const changes = S.inbox.filter(i => i.state === 'changes_requested');
  if (changes.length) body += `<div class="grp">${pill('warn', 'Changes requested')}${changes.map(i => `<div class="entry"><div class="entry__top">${ic('alert')}<span>${esc((person(i.person_id) || {}).name || '')} · ${esc((job(i.job_id) || {}).tag || '')} · ${esc(i.change)}</span></div><div class="entry__text">${esc(i.change_note)}</div></div>`).join('')}</div>`;
  const approved = S.inbox.filter(i => i.state === 'approved' && i.lock);
  if (approved.length) body += `<div class="grp">${pill('ok', 'Approved recently', 'lock')}${approved.map(i => entryHTML({ by: i.lock.by, at: i.lock.at, fingerprint: i.lock.fingerprint, text: `${(person(i.person_id) || {}).name || ''} · ${(job(i.job_id) || {}).tag || ''} · ${i.change}` })).join('')}</div>`;
  const pm = pmPerson();
  openDrawer({ key: 'intray', eyebrow: pm ? pm.name : 'In-tray', title: 'In-tray', sub: `${w.length} report${w.length === 1 ? '' : 's'} waiting. The manager’s Assistant checked each one against the original files.`, body });
}
function askApprove(ids) {
  const items = ids.map(id => S.inbox.find(x => x.id === id)).filter(Boolean);
  const flagged = items.some(i => i.group === 'flagged');
  showConfirm({
    title: 'Approve and lock?', okLabel: 'Approve and lock', okClass: 'btn--ok', icon: 'lock',
    body: `<p>This becomes a permanent record. Mistakes can be corrected later with an amendment, never erased.</p>
      <ul class="confirm-list">${items.map(i => `<li>${esc((person(i.person_id) || {}).name || '')} · ${esc((job(i.job_id) || {}).tag || '')} · <span class="mono">${esc(i.change)}</span></li>`).join('')}</ul>
      ${flagged ? `<p class="warnline">${ic('flag')}This report is flagged. The flag stays in the record.</p>` : ''}
      <p>You’re approving as ${esc(ROLES[S.me.role].label.toLowerCase())}.</p>`,
    onOk: () => approve(ids),
  });
}
async function approve(ids) {
  let res;
  try {
    res = ids.length > 1 ? (await api('POST', '/api/reports/approve', { ids })).approved : [await api('POST', `/api/reports/${ids[0]}/approve`)];
  } catch (e) { fail(e); return; }
  await stamp('Approved · locked');
  const firstItem = S.inbox.find(x => x.id === ids[0]);
  const prj = firstItem && job(firstItem.job_id) && job(firstItem.job_id).project_id;
  await refresh();
  const sh = prj && $('#sheet-' + prj);
  if (sh) { await flyFolder($('#intray'), sh, ids.length > 1 ? `${ids.length} locked` : (firstItem.change || 'Locked')); sh.classList.add('pop'); }
  toast(ids.length > 1 ? `${ids.length} reports approved and locked.` : `Approved and locked as ${res[0].fingerprint}. Progress updated.`, 'lock');
}

/* ================= project drawer + amend ================= */
let projectCache = {};
async function openProject(prid, quiet) {
  let pr;
  try { pr = await api('GET', `/api/projects/${prid}`); } catch (e) { if (!quiet) fail(e); return; }
  projectCache[prid] = pr;
  const team = pr.team.map(person).filter(Boolean);
  const body = `
    <section class="sec">${bar(pr.pct[0], pr.pct[1])}<div class="linkrow">${pr.plan_status === 'review' ? pill('warn', 'Plan waiting for your approval', 'clock') : pr.plan_status === 'approved' ? pill('ok', 'Plan approved · locked', 'lock') : pill('none', 'No plan yet')}<span class="where">${ic('briefcase')}${esc(pr.client || '')}</span></div>
      ${pr.plan_status === 'review' ? `<button class="btn btn--primary btn--lg" data-act="plan-review" data-proj="${pr.id}">Review the plan</button>` : ''}</section>
    <section class="sec"><div class="sec__head"><h3 class="h3">Jobs</h3><span class="eyebrow">${pr.jobs.length}</span></div>${pr.jobs.map(j => jobCard(j, { who: true })).join('') || `<p class="empty">Jobs appear here once the plan is approved.</p>`}</section>
    <section class="sec"><h3 class="h3">People</h3><div class="chips">${team.map(p => `<button class="chip-btn" data-act="open-person" data-id="${p.id}">${avatar(p, 'av--sm')}${esc(shown(p))}</button>`).join('') || '<p class="empty">Nobody yet.</p>'}</div></section>
    <section class="sec"><div class="sec__head"><h3 class="h3">History</h3><span class="eyebrow">Locked entries</span></div>${pr.history.map(h => entryHTML(h, pr.id)).join('') || '<p class="empty">Nothing approved yet.</p>'}</section>`;
  openDrawer({ key: 'project:' + prid, eyebrow: 'Project', title: pr.name, sub: `${pr.jobs.length} jobs · ${team.length} people`, body });
}
function amendForm(prid, entryId) {
  const h = (projectCache[prid].history || []).find(x => x.id === entryId);
  return `<div class="panel" style="margin-top:10px"><h4 class="h3">${ic('pen')}Amend this entry</h4>
    <label class="field"><span>Corrected entry</span><input id="amT" value="${esc(h.text)}"></label>
    ${h.delta != null ? `<label class="field"><span>Corrected quantity</span><input id="amD" class="mono" inputmode="decimal" value="${esc(h.delta)}"></label>` : ''}
    <label class="field"><span>Reason (required)</span><textarea id="amW" placeholder="What was wrong, and how you know"></textarea><span class="err" id="amErr" hidden>Add a reason. Amendments always say why.</span></label>
    <div class="panel__acts"><button class="btn btn--quiet" data-act="amend-cancel" data-entry="${entryId}">Cancel</button><button class="btn btn--primary" data-act="amend-preview" data-proj="${prid}" data-entry="${entryId}">Preview</button></div></div>`;
}

/* ================= ask ================= */
let CHAT = [];
function msgHTML(m) {
  if (m.me) return `<div class="msg msg--me">${esc(m.text)}</div>`;
  return `<div class="msg msg--bot"><span class="who">${asstBadge()}Assistant${m.by === 'rules' ? ' · rules engine' : ''}</span><span>${esc(m.text)}</span>${m.sources && m.sources.length ? `<div class="chips">${m.sources.map(s => `<span class="tag">${esc(s)}</span>`).join('')}</div>` : ''}</div>`;
}
function openAsk() {
  const me = person(S.me.id);
  openDrawer({
    key: 'ask', lead: avatar(me, 'av--lg'), eyebrow: 'Your Assistant', title: 'Ask about any job', sub: 'Answers come from the job records, with sources.',
    body: `<div class="chat" id="chat">${CHAT.length ? CHAT.map(msgHTML).join('') : `<p class="empty">Ask about a job, a person or a report. Try one of these:</p>`}</div><div class="suggest">${S.suggested_questions.map(q => `<button class="chip-btn" data-act="ask-q" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>`,
    foot: `<form class="composer" id="askForm"><input id="askInput" placeholder="Ask about a job, a person or a report" aria-label="Question" autocomplete="off"><button class="btn btn--primary" type="submit">${ic('send')}Ask</button></form>`,
  });
}
async function ask(q) {
  if (!q.trim()) return;
  CHAT.push({ me: true, text: q });
  const render = () => { if (drawerKey === 'ask') { $('#chat').innerHTML = CHAT.map(msgHTML).join(''); const b = $('#drawerBody'); b.scrollTop = b.scrollHeight; } };
  CHAT.push({ text: 'Looking through the job records…', by: '' }); render();
  try { const r = await api('POST', '/api/ask', { question: q }); CHAT[CHAT.length - 1] = { text: r.answer, sources: r.sources, by: r.answered_by }; }
  catch (e) { CHAT[CHAT.length - 1] = { text: e.message, by: '' }; }
  render();
}

/* ================= evidence ================= */
function openEvidence(t) {
  const blob = t.dataset.blob, kind = t.dataset.kind, label = t.dataset.label;
  let body;
  if (!blob) body = `<p class="explain">${esc(label)}</p><p class="quietline">This came in as text, so there is no file to show.</p>`;
  else if (kind === 'photo') body = `<div class="evview"><img class="evimg" src="/api/files/${blob}" alt="${esc(label)}"></div>`;
  else if (kind === 'voice') body = `<div class="evview"><audio controls src="/api/files/${blob}" style="width:100%"></audio></div>`;
  else body = `<div class="evview"><iframe class="evpdf" src="/api/files/${blob}" title="${esc(label)}"></iframe></div><p class="quietline"><a href="/api/files/${blob}" target="_blank" rel="noopener">Open the original in a new tab</a></p>`;
  openModal({ eyebrow: 'Evidence · original file', title: label || 'File', sub: 'Exactly what was sent, not a summary.', body, foot: `<button class="btn btn--primary" data-act="close-modal">Done</button>` });
}

/* ================= new project ================= */
let NP = null;
function openNewProject() {
  NP = { step: 1, name: '', client: '', types: new Set(['Drainage']), team: new Set(S.people.filter(p => p.role === 'pm').map(p => p.id)), files: [] };
  renderNP();
}
function npSteps() { return `<ol class="steps" aria-label="Steps">${['Name', 'Team', 'Documents', 'Plan'].map((s, i) => `<li class="${i + 1 === NP.step ? 'on' : i + 1 < NP.step ? 'done' : ''}" ${i + 1 === NP.step ? 'aria-current="step"' : ''}>${s}</li>`).join('')}</ol>`; }
function renderNP() {
  let body = npSteps(), foot = '';
  if (NP.step === 1) {
    body += `<label class="field"><span>Project name</span><input id="npName" value="${esc(NP.name)}" autocomplete="off" placeholder="Elm Street Drainage Improvements"><span class="err" id="npErr" hidden>Give the project a name.</span></label>
      <label class="field"><span>Client or agency</span><input id="npClient" value="${esc(NP.client)}" autocomplete="off" placeholder="City of Oak Hollow"></label>
      <fieldset class="field"><legend>Kind of work</legend><div class="toggles">${['Water', 'Sewer', 'Drainage', 'Paving'].map(t => `<button type="button" class="tog" aria-pressed="${NP.types.has(t)}" data-act="np-type" data-t="${t}">${ic(NP.types.has(t) ? 'check' : 'plus')}${t}</button>`).join('')}</div></fieldset>`;
    foot = `<button class="btn btn--quiet" data-act="close-modal">Cancel</button><button class="btn btn--primary btn--lg" data-act="np-next">Next: pick the team${ic('arrow')}</button>`;
  } else if (NP.step === 2) {
    body += `<p class="explain">Everyone you pick gets the scope documents for this project, and nothing else.</p><div class="teamgrid">${S.people.filter(p => !isMe(p)).map(p => `<button type="button" class="pick" aria-pressed="${NP.team.has(p.id)}" data-act="np-team" data-id="${p.id}">${avatar(p)}<span class="pick__t">${esc(p.name)}<small>${esc(p.company || p.role_label)}</small></span>${NP.team.has(p.id) ? ic('check') : ''}</button>`).join('')}</div>`;
    foot = `<button class="btn btn--quiet" data-act="np-back">Back</button><button class="btn btn--primary btn--lg" data-act="np-next">Next: add documents${ic('arrow')}</button>`;
  } else {
    body += `<label class="drop" id="drop" for="npFiles">${ic('upload')}<b>Drop plans, specs and bid forms</b><span>PDFs are read for quantities. Other files are kept with the project.</span><input type="file" id="npFiles" class="vh" multiple></label>
      <ul class="files">${NP.files.map((f, k) => `<li class="file"><span class="ev__thumb">${ic('file')}</span><div>${esc(f.name)}<small>${(f.file.size / 1e6).toFixed(1)} MB</small></div><button class="btn btn--quiet" data-act="np-unfile" data-k="${k}" aria-label="Remove ${esc(f.name)}">${ic('x')}</button></li>`).join('') || '<li class="empty">No files yet. You can also start without documents and type the quantities in.</li>'}</ul>
      <div class="connect"><button type="button" class="chip-btn" data-act="np-sample">${ic('file')}Add a sample bid form</button></div>
      <p class="quietline">Already-uploaded files are recognised and skipped. Reading runs in the background; you can keep working.</p>`;
    foot = `<button class="btn btn--quiet" data-act="np-back">Back</button><button class="btn btn--primary btn--lg" data-act="np-create">${ic('check')}Create project</button>`;
  }
  openModal({ eyebrow: 'New project', title: ['Name the project', 'Pick the team', 'Add scope documents'][NP.step - 1], sub: `Step ${NP.step} of 4`, body, foot });
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
function readNP1() { const n = $('#npName'), c = $('#npClient'); if (n) NP.name = n.value.trim(); if (c) NP.client = c.value.trim(); }
async function createProject() {
  const room = $('#room-plan'), cab = $('#cabinet'), cc = $('#cabCount'), tbl = $('#ptable');
  let pr, docs = [];
  try {
    pr = await api('POST', '/api/projects', { name: NP.name, client: NP.client || null, work_types: [...NP.types], team: [...NP.team] });
    if (NP.files.length) { const fd = new FormData(); NP.files.forEach(f => fd.append('files', f.file, f.name)); docs = (await api('POST', `/api/projects/${pr.id}/documents`, fd, true)).documents; }
  } catch (e) { fail(e); return; }
  closeModal();
  nearView(room); room.classList.add('is-busy'); await sleep(400);
  cab.classList.add('open');
  for (const d of docs.filter(x => x.status === 'ready').slice(0, 5)) { flyFolder($('#newProjectBtn'), cab, d.filename.replace(/\.[a-z]+$/i, '')); await sleep(220); }
  await sleep(docs.length ? 1300 : 300); cab.classList.remove('open');
  const proposed = new Promise(res => planWaiters.set(pr.id, res));
  try { await api('POST', `/api/projects/${pr.id}/plan`); } catch (e) { fail(e); room.classList.remove('is-busy'); return; }
  const pages = docs.filter(x => x.status === 'ready').reduce((s, d) => s + (d.pages || 0), 0);
  const dups = docs.filter(x => x.status === 'duplicate').length;
  cc.hidden = false; tbl.classList.add('unroll');
  const t0 = performance.now(), minDur = reduce ? 1 : 2200;
  let done = false; proposed.then(() => { done = true; });
  const poll = setInterval(async () => { try { const pl = await api('GET', `/api/projects/${pr.id}/plan`); if (pl.project.plan_status === 'review') done = true; } catch { /* keep waiting */ } }, 1200);
  await new Promise(res => {
    const step = (t) => {
      const k = Math.min(done ? 1 : .92, (t - t0) / minDur);
      cc.textContent = pages ? `Reading ${Math.round(pages * k)} of ${pages} pages` : 'Setting up the plan';
      if (k >= 1 && done) res(); else requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
  clearInterval(poll); planWaiters.delete(pr.id);
  cc.textContent = pages ? `Read ${pages} pages${dups ? ` · ${dups} duplicate skipped` : ''}` : 'Plan ready';
  let plan;
  try { plan = await api('GET', `/api/projects/${pr.id}/plan`); } catch (e) { fail(e); return; }
  const board = $('#board'); const br = board.getBoundingClientRect();
  const notes = [];
  for (let k = 0; k < plan.items.length; k++) {
    const it = plan.items[k];
    const n = make('div', 'sticky', `<b>${it.method === 'done' ? 'Done / not' : it.quantity != null ? fmt(it.quantity) + ' ' + esc(it.unit) : '? ' + esc(it.unit)}</b>${esc(it.tag)}`);
    const cols = Math.max(2, Math.floor((br.width - 20) / 70));
    n.style.left = (10 + (k % cols) * ((br.width - 116) / Math.max(1, cols - 1))) + 'px';
    n.style.top = (6 + Math.floor(k / cols) * 34) + 'px';
    n.style.setProperty('--r', ((k % 3) - 1) * 3 + 'deg');
    board.appendChild(n); notes.push(n); await sleep(220);
  }
  await sleep(800);
  await refresh();
  const sheet = $('#sheet-' + pr.id);
  if (sheet && !reduce) {
    const sr = sheet.getBoundingClientRect();
    await Promise.all(notes.map(n => { const r = n.getBoundingClientRect(); return n.animate([{ transform: 'none' }, { transform: `translate(${sr.left - r.left}px,${sr.top - r.top}px) scale(.3)`, opacity: 0 }], { duration: 600, easing: 'ease-in', fill: 'forwards' }).finished; }));
    sheet.classList.add('pop');
  }
  notes.forEach(n => n.remove());
  cc.hidden = true; room.classList.remove('is-busy');
  openPlanReview(pr.id, plan);
}
async function openPlanReview(prid, plan) {
  if (!plan) { try { plan = await api('GET', `/api/projects/${prid}/plan`); } catch (e) { fail(e); return; } }
  const notes = plan.project.notes || [];
  const body = `<p class="explain">The Assistant read the scope documents and proposes these items. Change anything before you approve. Nothing counts toward progress until a report is approved.</p>
    ${notes.length ? `<ul class="notes">${notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
    ${plan.items.map(it => `<div class="planitem ${it.method === 'qty' && it.quantity == null ? 'qty-missing' : ''}"><div class="planitem__name"><span>${esc(it.name)}</span><span class="tag">${esc(it.source)}</span></div>
      <div class="planitem__row">
        ${it.method === 'done' ? `<div class="field"><span>Measured by</span><span class="pill pill--none" style="align-self:flex-start">${ic('check')}Done or not</span></div>` : `<label class="field"><span>Quantity (${esc(it.unit)})</span><input id="pi-q-${it.id}" class="mono" inputmode="decimal" value="${it.quantity != null ? it.quantity : ''}" placeholder="From the plans">${it.quantity == null ? '<span class="hint">Not in the documents. Enter it from the plans.</span>' : ''}</label>`}
        <label class="field"><span>Who reports it</span><select id="pi-w-${it.id}">${plan.team.map(p => `<option value="${p.id}" ${p.id === it.person_id ? 'selected' : ''}>${esc(p.name)} · ${esc(p.role_label)}</option>`).join('')}</select></label>
      </div></div>`).join('')}
    <p class="err" id="planErr" hidden></p>
    <p class="quiet">Quantities come from the scope documents. Each item keeps its source.</p>`;
  openDrawer({ key: 'plan:' + prid, eyebrow: 'New project · step 4 of 4', title: 'Review the plan', sub: plan.project.name, body, foot: `<button class="btn btn--quiet" data-act="close-drawer">Not now</button><button class="btn btn--ok btn--lg" data-act="plan-approve" data-proj="${prid}">${ic('lock')}Approve plan</button>` });
  drawerPlan = plan;
}
let drawerPlan = null;
function readPlan() {
  return drawerPlan.items.map(it => {
    const q = $('#pi-q-' + it.id), w = $('#pi-w-' + it.id);
    const out = { id: it.id };
    if (q && q.value.trim()) out.quantity = parseFloat(q.value.replace(/[^\d.]/g, ''));
    if (w) out.person_id = +w.value;
    return out;
  });
}
async function approvePlan(prid, items) {
  let res;
  try { res = await api('POST', `/api/projects/${prid}/plan/approve`, { items }); }
  catch (e) { const er = $('#planErr'); if (er) { er.hidden = false; er.textContent = e.message; er.scrollIntoView({ block: 'nearest' }); } else fail(e); return; }
  await stamp('Plan approved');
  closeDrawer();
  const sheet = $('#sheet-' + prid);
  await refresh();
  const byWho = {};
  S.jobs.filter(j => j.project_id === prid && j.assignee_id).forEach(j => { (byWho[j.assignee_id] = byWho[j.assignee_id] || []).push(j); });
  for (const [pid, js] of Object.entries(byWho)) {
    const ws = $('#ws-' + pid); if (!ws) continue;
    nearView(ws);
    await flyTag($('#sheet-' + prid) || sheet, ws, js[0].tag, (project(prid) || {}).color || '#5B6475');
    await sleep(100);
  }
  $('#ptable').classList.remove('unroll');
  toast(`Plan approved and locked as ${res.fingerprint}. Progress tracking starts now.`, 'lock');
}

/* ================= actions ================= */
function openAssign(jid) {
  const j = job(jid);
  openDrawer({ key: 'assign:' + jid, eyebrow: 'Assign a job', title: j.name, sub: 'Who should report on this?', body: `<div class="picklist">${S.people.filter(p => !isMe(p)).map(p => `<button class="pick" data-act="assign-to" data-job="${jid}" data-id="${p.id}">${avatar(p)}<span class="pick__t">${esc(p.name)}<small>${esc(p.company || p.role_label)} · ${jobsOf(p.id).length} jobs</small></span>${ic('arrow')}</button>`).join('')}</div>` });
}
const ACTS = {
  'close-drawer': () => closeDrawer(),
  'close-modal': () => closeModal(),
  'open-person': (t) => { closeModal(); openPerson(+t.dataset.id); },
  'open-intray': () => { closeModal(); openIntray(); },
  'open-project': (t) => openProject(+t.dataset.proj),
  'toggle-live': (t) => { live = !live; t.setAttribute('aria-pressed', live); $('span', t).textContent = live ? 'Live' : 'Paused'; toast(live ? 'Animations on. New activity plays as it arrives.' : 'Animations paused. Updates still come in.', live ? 'play' : 'pause'); },
  'hire': (t) => openHire(t.dataset.room),
  'h-job': (t) => { readHire(); const id = +t.dataset.job; H.jobs.has(id) ? H.jobs.delete(id) : H.jobs.add(id); t.setAttribute('aria-pressed', H.jobs.has(id)); },
  'hire-go': () => hire(),
  'copy-link': async () => { const txt = $('#fieldLink').textContent; try { await navigator.clipboard.writeText(txt); toast('Link copied.'); } catch { const r = document.createRange(); r.selectNodeContents($('#fieldLink')); getSelection().removeAllRanges(); getSelection().addRange(r); toast('Press Ctrl+C or ⌘C to copy.', 'link'); } },
  'new-link': async (t) => { try { const r = await api('POST', `/api/people/${t.dataset.id}/link`); const p = person(+t.dataset.id); openModal({ eyebrow: 'Field app link', title: `New link for ${p.name}`, sub: 'Send it to them; it opens their Assistant.', body: `<div class="linkbox"><code id="fieldLink">${esc(r.field_link)}</code><div class="linkrow"><button class="btn btn--primary" data-act="copy-link">${ic('link')}Copy link</button></div></div>`, foot: `<button class="btn btn--primary" data-act="close-modal">Done</button>` }); } catch (e) { fail(e); } },
  'new-project': () => openNewProject(),
  'np-type': (t) => { readNP1(); const x = t.dataset.t; NP.types.has(x) ? NP.types.delete(x) : NP.types.add(x); renderNP(); },
  'np-team': (t) => { const id = +t.dataset.id; NP.team.has(id) ? NP.team.delete(id) : NP.team.add(id); renderNP(); },
  'np-next': () => { if (NP.step === 1) { readNP1(); if (!NP.name) { $('#npErr').hidden = false; $('#npName').focus(); return; } } NP.step++; renderNP(); },
  'np-back': () => { NP.step--; renderNP(); },
  'np-unfile': (t) => { NP.files.splice(+t.dataset.k, 1); renderNP(); },
  'np-sample': async () => { try { const r = await fetch('/static/samples/elm-street-bid-form.pdf'); const b = await r.blob(); NP.files.push({ name: 'Elm St bid form.pdf', file: new File([b], 'Elm St bid form.pdf', { type: 'application/pdf' }) }); renderNP(); } catch (e) { fail(e); } },
  'np-create': () => createProject(),
  'plan-review': (t) => openPlanReview(+t.dataset.proj),
  'plan-approve': (t) => { const items = readPlan(); showConfirm({ title: 'Approve and lock this plan?', okLabel: 'Approve plan', okClass: 'btn--ok', icon: 'lock', body: `<p>${items.length} items become the baseline that progress is measured against. Changes later go in as amendments, never erased.</p>`, onOk: () => approvePlan(+t.dataset.proj, items) }); },
  'assign-job': (t) => openAssign(+t.dataset.job),
  'assign-to': async (t) => { closeDrawer(); try { await assignAnimated(+t.dataset.id, +t.dataset.job, $('#needs')); } catch (e) { fail(e); } },
  'add-job-open': (t) => { AJ = { pid: +t.dataset.id, mode: 'existing', pick: null, method: 'qty', unit: 'LF' }; renderAJ(); $('#addJobArea').scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest' }); },
  'aj-mode': (t) => { keepAJFields(); AJ.mode = t.dataset.v; renderAJ(); },
  'aj-pick': (t) => { AJ.pick = +t.dataset.job; $$('[data-act="aj-pick"]').forEach(b => b.setAttribute('aria-pressed', +b.dataset.job === AJ.pick)); },
  'aj-method': (t) => { keepAJFields(); AJ.method = t.dataset.v; renderAJ(); },
  'aj-cancel': () => { const pid = AJ.pid; AJ = null; $('#addJobArea').innerHTML = `<button class="btn btn--primary btn--lg btn--block" data-act="add-job-open" data-id="${pid}">${ic('plus')}Add a job</button>`; },
  'aj-save': async (t) => {
    const err = $('#ajErr');
    try {
      if (AJ.mode === 'existing') {
        if (!AJ.pick) { toast('Pick a job first.', 'alert'); return; }
        const pid = AJ.pid, jid = AJ.pick; AJ = null;
        await assignAnimated(pid, jid, t);
      } else {
        keepAJFields();
        const total = parseFloat(String(AJ.total || '').replace(/[^\d.]/g, ''));
        const created = await api('POST', '/api/jobs', { project_id: +$('#ajProj').value, name: (AJ.name || '').trim(), method: AJ.method, total: AJ.method === 'qty' ? total : null, unit: AJ.unit || 'LF', assignee_id: null });
        const pid = AJ.pid; AJ = null;
        await refresh();
        await assignAnimated(pid, created.id, t);
      }
    } catch (e) { if (err) { err.hidden = false; err.textContent = e.message; } else fail(e); }
  },
  'unassign': async (t) => { try { await api('PATCH', `/api/jobs/${t.dataset.job}`, { assignee_id: null }); await refresh(); } catch (e) { fail(e); } },
  'save-details': async (t) => { try { await api('PATCH', `/api/people/${t.dataset.id}`, { name: $('#pName').value, contact: $('#pContact').value }); await refresh(); toast('Details saved.'); } catch (e) { fail(e); } },
  'ask-remove': (t) => { const p = person(+t.dataset.id); const n = jobsOf(p.id).length; showConfirm({ title: `Remove ${p.name}?`, okLabel: 'Remove from office', okClass: 'btn--primary', icon: 'trash', body: `<p>Their Assistant stops taking messages and their field link stops working. Approved history stays locked in the record.${n ? ` ${n} job${n === 1 ? '' : 's'} will need someone new.` : ''}</p>`, onOk: () => removePerson(p.id) }); },
  'approve': (t) => askApprove([+t.dataset.id]),
  'approve-batch': () => askApprove(waiting().filter(i => i.group === 'routine').map(i => i.id)),
  'ask-item': (t) => { const i = S.inbox.find(x => x.id === +t.dataset.id); $('#inl-' + i.id).innerHTML = `<div class="panel"><h4 class="h3">${ic('chat')}Ask ${esc((person(i.person_id) || {}).name || '')}</h4><div class="suggest">${i.questions.map(q => `<button class="chip-btn" data-act="send-q" data-id="${i.id}" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div><label class="field"><span>Or write your own</span><input id="q-${i.id}" placeholder="Type a question"></label><div class="panel__acts"><button class="btn btn--quiet" data-act="inline-cancel" data-id="${i.id}">Cancel</button><button class="btn btn--primary" data-act="send-q" data-id="${i.id}">${ic('send')}Send question</button></div></div>`; },
  'req-changes': (t) => { const i = S.inbox.find(x => x.id === +t.dataset.id); const miss = i.missing.length ? i.missing : i.flags.map(f => f.text).slice(0, 2); const list = miss.length ? miss : ['A closer look']; $('#inl-' + i.id).innerHTML = `<div class="panel"><h4 class="h3">${ic('alert')}Request changes</h4><div class="picklist">${list.map((m, k) => `<label class="role" style="padding-left:44px"><input type="checkbox" checked data-miss="${k}" value="${esc(m)}"><b>${esc(m)}</b></label>`).join('')}</div><label class="field"><span>Note (optional)</span><textarea id="cn-${i.id}" placeholder="Anything else they should know"></textarea></label><div class="panel__acts"><button class="btn btn--quiet" data-act="inline-cancel" data-id="${i.id}">Cancel</button><button class="btn btn--primary" data-act="send-changes" data-id="${i.id}">${ic('send')}Send back</button></div></div>`; },
  'inline-cancel': (t) => { $('#inl-' + t.dataset.id).innerHTML = ''; },
  'send-q': async (t) => { const id = +t.dataset.id; const own = $('#q-' + id); const q = t.dataset.q || (own && own.value.trim()); if (!q) { own && own.focus(); return; } try { await api('POST', `/api/reports/${id}/questions`, { text: q }); toast('Question sent. It’s pinned to the report.', 'send'); $('#inl-' + id).innerHTML = ''; await refresh(); openIntray(); } catch (e) { fail(e); } },
  'send-changes': async (t) => { const id = +t.dataset.id; const inl = $('#inl-' + id); const items = $$('input[data-miss]', inl).filter(c => c.checked).map(c => c.value); try { await api('POST', `/api/reports/${id}/changes`, { items, note: $('#cn-' + id).value }); toast('Sent back with the missing list.', 'send'); $('#inl-' + id).innerHTML = ''; await refresh(); openIntray(); } catch (e) { fail(e); } },
  'evidence': (t) => openEvidence(t),
  'ask-q': (t) => ask(t.dataset.q),
  'amend': (t) => { $('#amend-' + t.dataset.entry).innerHTML = amendForm(+t.dataset.proj, +t.dataset.entry); },
  'amend-cancel': (t) => { $('#amend-' + t.dataset.entry).innerHTML = ''; },
  'amend-preview': (t) => {
    const prid = +t.dataset.proj, eid = +t.dataset.entry;
    const h = projectCache[prid].history.find(x => x.id === eid);
    const nt = $('#amT').value.trim(), why = $('#amW').value.trim(), dEl = $('#amD');
    const delta = dEl && dEl.value.trim() ? parseFloat(dEl.value) : null;
    if (!why) { $('#amErr').hidden = false; $('#amW').focus(); return; }
    showConfirm({ title: 'Add this amendment?', okLabel: 'Amend and lock', okClass: 'btn--primary', icon: 'pen', body: `<div class="entry"><div class="entry__text struck">${esc(h.text)}</div><div class="amend"><div class="amend__by">Amended by you · now</div><div class="entry__text">${esc(nt)}</div><div class="amend__by">Reason: ${esc(why)}</div></div></div><p>The original stays readable under the line. Both are locked.</p>`, onOk: async () => { try { await api('POST', `/api/ledger/${eid}/amend`, { text: nt, reason: why, delta }); toast('Amendment added. The original stays on the record.', 'pen'); await refresh(); openProject(prid); } catch (e) { fail(e); } } });
  },
};
document.addEventListener('click', (e) => {
  const ws = e.target.closest('.ws[data-person]');
  if (ws) { openPerson(+ws.dataset.person); return; }
  const t = e.target.closest('[data-act]');
  if (t && ACTS[t.dataset.act]) { e.preventDefault(); ACTS[t.dataset.act](t, e); }
});
document.addEventListener('change', (e) => {
  const r = e.target.closest('fieldset[data-role-for] input[type="radio"]');
  if (r) changeRole(+r.closest('fieldset').dataset.roleFor, r.value);
});
document.addEventListener('submit', (e) => {
  if (e.target.id === 'askForm') { e.preventDefault(); const i = $('#askInput'); ask(i.value); i.value = ''; i.focus(); }
});
$('#scrim').addEventListener('click', () => { if (!$('#modal').hidden) closeModal(); else closeDrawer(); });
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const c = $('#drawer .confirm'); if (c) { c.remove(); return; }
  if (!$('#modal').hidden) closeModal(); else closeDrawer();
});

/* ================= boot ================= */
(async function boot() {
  try { S = adapt(await api('GET', '/api/office')); } catch (e) { if (e.status !== 401) fail(e); return; }
  ROOMS.forEach(r => { $('#desks-' + r).innerHTML = ''; });
  S.people.forEach(p => $('#desks-' + p.room).appendChild(wsEl(p)));
  roomCounts(); renderRail(); renderLog(false); updateClocks();
  setInterval(updateClocks, 20000);
  connect();
})();
})();
