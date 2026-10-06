'use strict';
const $ = s => document.querySelector(s), app = $('#app'), val = id => ($('#' + id) || {}).value || '';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(16).slice(2));
const today = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); };
async function api(path, body) {
  const opt = {method: body === undefined ? 'GET' : 'POST', headers: {'X-PG': '1'}};
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt); let d = {}; try { d = await r.json(); } catch (e) {}
  if (!r.ok) { const e = new Error(d.error || 'Something went wrong. Please try again.'); e.status = r.status; throw e; } return d;
}
let tt; function toast(m, bad) { const e = $('#toast'); e.textContent = m; e.className = bad ? 'bad' : ''; e.hidden = false; clearTimeout(tt); tt = setTimeout(() => e.hidden = true, 6000); }
/* run an action; on success show a message and refresh the current screen. A message starting "Owner confirmation required" asks you first, then repeats with confirm=true */
async function act(path, body, ok, after) {
  try { let r; try { r = await api(path, body); } catch (e) { if (e.message.startsWith('Owner confirmation required') && confirm(e.message + '\n\nContinue?')) r = await api(path, {...body, confirm: true}); else throw e; }
    if (ok) toast(ok); if (after) await after(r); else await show(); return r; }
  catch (e) { if (e.status === 401) return login(); toast(e.message, true); return null; }
}
const S = {pg: '', tab: 'dash', cf: 'open', cat: '', foodf: 'open', open: null, meta: null};
const TABS = [['dash', 'Dashboard'], ['res', 'Residents'], ['rooms', 'Rooms'], ['pay', 'Payments'], ['cmp', 'Complaints'], ['food', 'Food'], ['guest', 'Guests'], ['rep', 'Reports'], ['set', 'Settings']];
const shell = inner => `<div class="bar"><div class="grow">${esc(S.pg)}</div><button class="ghost" data-act="logout">Log out</button></div><nav class="tabs" aria-label="Sections">${TABS.map(([k, l]) => `<button class="tab" aria-selected="${k === S.tab}" data-act="tab" data-tab="${k}">${l}</button>`).join('')}</nav>${inner}`;
const kv = (k, v) => `<div class="kv"><span class="muted">${k}</span><b>${v}</b></div>`;
const opts = (arr, sel) => arr.map(a => { const [v, l] = Array.isArray(a) ? a : [a, a]; return `<option value="${esc(v)}" ${v === sel ? 'selected' : ''}>${esc(l)}</option>`; }).join('');
const origin = location.origin;

async function login(msg) {
  render(`<div class="bar"><div class="grow">PG Owner</div></div><h1>Owner login</h1>${msg ? `<p class="err">${esc(msg)}</p>` : ''}<label for="em">Email</label><input id="em" type="email" autocomplete="username"><label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password"><button class="btn" data-act="login">Log in</button>`);
}
function render(h) { app.innerHTML = h; window.scrollTo(0, 0); }
async function show() { const keep = scrollY; try { await VIEW[S.tab](); window.scrollTo(0, keep); } catch (e) { if (e.status === 401) login(); else toast(e.message || 'No connection. Please try again.', true); } }
async function meta() { return S.meta = await api('/api/owner/meta'); }

/* ---------------- Dashboard ---------------- */
const GO = {complaint: 'cmp', needs_review: 'cmp', duplicates: 'cmp', rent: 'pay', food: 'food', guest_approval: 'guest', guest_overstay: 'guest', guest_leaving: 'guest', guest_charge: 'pay', leave: 'guest', setup: 'set', deletion: 'res'};
async function dash() {
  const d = await api('/api/owner/dashboard'), g = d.glance, st = (l, v) => `<div class="stat"><div class="sv">${esc(v)}</div><div class="sl">${l}</div></div>`;
  render(shell(`<h1>What needs your attention today?</h1><div class="stats">${st('Residents', g.residents)}${st('Beds occupied', g.occupied_beds + ' of ' + g.total_beds)}${st('Vacant beds', g.vacant_beds)}${st('Rent pending', g.rent_pending)}${st('Rent overdue', g.rent_overdue)}${st('Open complaints', g.open_complaints + (g.overdue_complaints ? ' (' + g.overdue_complaints + ' overdue)' : ''))}</div>
  ${d.all_clear ? `<div class="card"><b>✓ All clear.</b> Nothing needs your attention right now.</div>` : d.needs_attention.map(i => `<button class="att ${i.severity}" data-act="goto" data-kind="${i.kind}"><span class="pill ${i.severity}">${esc(i.label)}</span><span>${esc(i.text)}${i.kind === 'not_delivered' ? `<br><span class="small muted">${i.detail.slice(0, 4).map(x => esc(x.to + ' — ' + x.reason)).join('<br>')}</span>` : ''}</span></button>`).join('')}
  ${d.hidden_items ? `<p class="muted">+${d.hidden_items} more items</p>` : ''}`));
}

/* ---------------- Residents ---------------- */
function linkPanel(path, name) {
  const url = origin + path, msg = `Hello ${name}, please open this link to join your PG app and choose a PIN: ${url}`;
  return `<div class="card"><b>Invite link for ${esc(name)}</b> (works once, for 7 days)<div class="link" id="lnk">${esc(url)}</div><button class="btn sm" data-act="copy" data-text="${esc(url)}">Copy link</button><a class="btn sm alt" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(msg)}">Share on WhatsApp</a></div>`;
}
async function res(extra = '') {
  const d = await api('/api/owner/residents');
  render(shell(`<h1>Residents</h1>${extra}<button class="btn" data-act="res-add-form">+ Add resident</button><div id="f"></div>${d.residents.length ? d.residents.map(r => `<button class="rowbtn" data-act="res-open" data-id="${r.id}"><b>${esc(r.name)}</b> ${r.status === 'invited' ? '<span class="pill grey">Invited</span>' : ''}${r.pending.paise ? `<span class="pill warn">Owes ${esc(r.pending.text)}</span>` : ''}<br><span class="muted">${r.room_number ? `Floor ${r.floor} · Room ${esc(r.room_number)} · Bed ${esc(r.bed)}` : 'No bed assigned yet'} · ${esc(r.phone)}</span></button>`).join('') : `<p class="muted">No residents yet. Add your first resident to get an invite link.</p>`}`));
}
async function resProfile(id, extra = '') {
  const r = await api('/api/owner/residents/' + id); await meta(); S.rid = id; const vb = S.meta.vacant_beds, bedSel = `<select id="bed">${opts(vb.map(b => [b.bed_id, `${b.label} (${b.rent.text})`]))}</select>`;
  render(shell(`<button class="ghost" data-act="tab" data-tab="res">← Residents</button><h1>${esc(r.name)}</h1>${extra}<div class="card">${kv('Phone', esc(r.phone))}${r.email ? kv('Email', esc(r.email)) : ''}${r.stay ? kv('Room', `Floor ${r.stay.floor} · Room ${esc(r.stay.room)} · Bed ${esc(r.stay.bed)}`) + kv('Monthly rent', r.stay.rent.text) + kv('Deposit', `${r.deposit_status.paid.text} of ${r.deposit_status.expected.text} (${esc(r.deposit_status.status)})`) + kv('Moved in', esc(r.move_in || '')) : kv('Bed', 'Not assigned')}${kv('Rent pending', r.pending.text)}${r.advance.paise ? kv('Advance paid', r.advance.text) : ''}</div>
  <button class="btn sm alt" data-act="invite" data-id="${r.id}">Get invite link</button>
  ${!r.stay && r.status !== 'moved_out' ? `<h2>Assign a bed</h2>${vb.length ? `<label for="bed">Vacant bed</label>${bedSel}<label for="mi">Move-in date</label><input id="mi" type="date" value="${today()}"><button class="btn" data-act="assign" data-id="${r.id}">Assign bed</button>` : '<p class="muted">No vacant beds. Add rooms in Settings.</p>'}` : ''}
  <h2>Record a payment</h2><div class="card"><div class="two"><div><label for="am">Amount (₹)</label><input id="am" type="number" inputmode="decimal" min="1"></div><div><label for="me">How paid</label><select id="me">${opts([['cash', 'Cash'], ['upi', 'UPI'], ['bank', 'Bank transfer'], ['other', 'Other']])}</select></div></div><div class="two"><div><label for="pd">Date received</label><input id="pd" type="date" value="${today()}" max="${today()}"></div><div><label for="pu">For</label><select id="pu">${opts([['rent', 'Rent'], ['deposit', 'Security deposit']])}</select></div></div><button class="btn" data-act="pay-save" data-id="${r.id}" data-key="${uid()}">Save payment</button></div>
  <h2>Charges</h2>${r.charges.length ? r.charges.map(c => `<div class="card">${kv(esc(c.kind === 'rent' ? 'Rent ' + c.period : c.note || c.kind), c.amount.text)}${kv('Due ' + esc(c.due), c.remaining.paise ? 'Remaining ' + c.remaining.text : '✓ Paid')}${c.remaining.paise || c.kind !== 'rent' ? `<button class="btn sm alt" data-act="void" data-id="${c.id}">Cancel this charge</button>` : ''}</div>`).join('') : '<p class="muted">No charges yet. Use Payments → Generate rent.</p>'}
  <h2>Payment history</h2>${r.payments.length ? r.payments.map(p => `<div class="card">${kv(esc(p.date) + ' · ' + esc(p.method) + ' · ' + esc(p.purpose), (p.type === 'reversal' ? '− ' : '') + p.amount.text.replace('-', ''))}${p.type === 'reversal' ? `<div class="small muted">Cancelled entry: ${esc(p.reason || '')}</div>` : ''}${p.can_reverse ? `<button class="btn sm alt" data-act="reverse" data-id="${p.id}">Cancel this entry</button>` : ''}</div>`).join('') : '<p class="muted">No payments yet.</p>'}
  ${r.stay ? `<h2>Move to another bed</h2><div class="card"><label for="bed2">New bed</label><select id="bed2">${opts(vb.map(b => [b.bed_id, `${b.label} (${b.rent.text})`]))}</select><label for="nr">New monthly rent (₹) — leave empty to keep ${r.stay.rent.text}</label><input id="nr" type="number" min="0"><button class="btn" data-act="move" data-id="${r.id}">Move resident</button></div>
  <h2>Move out</h2><div class="card"><label for="md">Move-out date</label><input id="md" type="date" value="${today()}"><div class="two"><div><label for="dl">Deduction (optional)</label><input id="dl" placeholder="e.g. Damage"></div><div><label for="da">Amount (₹)</label><input id="da" type="number" min="0"></div></div><button class="btn alt" data-act="mo-prepare" data-id="${r.id}">Calculate final figures</button><div id="mo"></div></div>` : ''}`));
}
/* ---------------- Rooms ---------------- */
async function rooms() {
  const d = await api('/api/owner/rooms'), by = {}; d.rooms.forEach(r => (by[r.floor] = by[r.floor] || []).push(r));
  render(shell(`<h1>Rooms</h1><div class="stats"><div class="stat"><div class="sv">${d.occupied}/${d.total_beds}</div><div class="sl">Beds occupied</div></div><div class="stat"><div class="sv">${d.vacant}</div><div class="sl">Vacant beds</div></div></div>${Object.keys(by).length ? Object.keys(by).map(f => `<h2>${f === '0' ? 'Ground floor' : 'Floor ' + f}</h2>${by[f].map(r => `<div class="card"><b>${esc(r.room)}</b> — ${r.occupied}/${r.capacity}${r.status !== 'active' ? ` <span class="pill grey">${esc(r.status)}</span>` : ''}<div class="bedrow">${r.beds.map(b => b.occupant ? `<span class="bed">Bed ${esc(b.bed)}: ${esc(b.occupant)}</span>` : `<span class="bed vac">Bed ${esc(b.bed)}: Vacant</span>`).join('')}</div></div>`).join('')}`).join('') : `<p class="muted">No rooms yet. Add them in Settings.</p>`}`));
}
/* ---------------- Payments ---------------- */
async function pay() {
  const d = await api('/api/owner/payments');
  render(shell(`<h1>Payments</h1><div class="stats"><div class="stat"><div class="sv">${d.total_pending.text}</div><div class="sl">Rent pending</div></div><div class="stat"><div class="sv">${d.total_overdue.text}</div><div class="sl">Overdue</div></div></div>
  <div class="card"><b>Generate rent for a month</b><p class="muted small">Adds one rent charge for every current resident. Running it twice never duplicates.</p><label for="pm">Month</label><input id="pm" type="month" value="${d.period}"><button class="btn" data-act="gen">Generate rent</button></div>
  <h2>Who owes rent</h2>${d.rows.length ? d.rows.map(r => `<button class="rowbtn" data-act="res-open" data-id="${r.tenant_id}"><b>${esc(r.name)}</b> <span class="pill ${r.status === 'overdue' ? 'bad' : 'warn'}">${r.status === 'overdue' ? 'Overdue ' + esc(r.overdue.text) : 'Due soon'}</span><br>Pending ${esc(r.pending.text)}</button>`).join('') : '<p class="muted">Nobody owes rent. ✓</p>'}<p class="muted small">Open a resident to record a payment or cancel a wrong entry.</p>`));
}
/* ---------------- Complaints / Food ---------------- */
const PCLS = {URGENT: 'red', HIGH: 'orange', MEDIUM: 'yellow', LOW: 'grey'}, SLAB = {needs_review: 'Needs review', new: 'New', acknowledged: 'Seen', assigned: 'Assigned', in_progress: 'In progress', waiting: 'Waiting', resolved: 'Waiting for resident to confirm', closed: 'Closed'};
const QF = {open: 'open=1', urgent: 'open=1&priority=URGENT', overdue: 'open=1&overdue=1', review: 'status=needs_review', awaiting: 'status=resolved', all: ''};
const QL = {open: 'Open', urgent: 'Urgent', overdue: 'Overdue', review: 'Needs review', awaiting: 'Awaiting resident', all: 'All'};
function card(c) {
  const m = S.meta, o = S.open === c.id;
  return `<div class="card"><span class="pill ${PCLS[c.priority]}">${c.priority}</span> ${c.overdue ? '<span class="pill red">OVERDUE</span>' : ''}${c.reopened ? '<span class="pill orange">REOPENED</span>' : ''}${c.danger_word ? '<span class="pill red">DANGER WORD</span>' : ''}<br><b>#${c.id} · ${c.room ? 'Room ' + esc(c.room) : esc(c.tenant)} · ${esc(c.category || 'Needs sorting')}</b><div>${esc(c.text)}</div><div class="muted small">${SLAB[c.status] || c.status} · open ${c.hours_open}h · ${c.staff ? 'Assigned to ' + esc(c.staff) : 'Not assigned'}${c.ai_state === 'tenant_selected' ? ' · category chosen by resident' : c.ai_state === 'ok' ? ' · sorted by AI' : ''}</div>
  ${c.duplicate_of ? `<button class="btn sm alt" data-act="merge" data-id="${c.id}" data-into="${c.duplicate_of}">Same as #${c.duplicate_of} — merge</button>` : ''}<button class="btn sm alt" data-act="manage" data-id="${c.id}">${o ? 'Close' : 'Manage'}</button>
  ${o ? `<div><label>Category (tap to fix)</label><div class="chips">${m.categories.map(k => `<button class="chip" data-act="cat" data-id="${c.id}" data-val="${esc(k)}" aria-pressed="${k === c.category}">${esc(k)}</button>`).join('')}</div>
  ${c.category ? `<label>Priority</label><div class="chips">${['LOW', 'MEDIUM', 'HIGH', 'URGENT'].map(p => `<button class="chip" data-act="prio" data-id="${c.id}" data-cat="${esc(c.category)}" data-val="${p}" aria-pressed="${p === c.priority}">${p}</button>`).join('')}</div>
  <label for="st${c.id}">Assign to</label><select id="st${c.id}">${opts([['', 'Choose…'], ...m.staff.map(x => [x.id, `${x.name} (${x.role})`])], String(c.staff_id || ''))}</select><button class="btn sm" data-act="assign-c" data-id="${c.id}">Assign</button>
  <label>Status</label><div>${[['acknowledged', 'Seen'], ['in_progress', 'In progress'], ['waiting', 'Waiting'], ['resolved', 'Mark resolved']].map(([v, l]) => `<button class="btn sm alt" data-act="cstatus" data-id="${c.id}" data-val="${v}">${l}</button>`).join('')}</div><p class="muted small">A complaint closes only when the resident confirms it is fixed.</p>` : '<p class="muted small">Choose a category first.</p>'}</div>` : ''}</div>`;
}
async function cmpList(filters, key) {
  await meta(); const d = await api('/api/owner/complaints?' + filters);
  return d.complaints.length ? d.complaints.map(card).join('') : `<p class="muted">Nothing here.</p>`;
}
async function cmp() {
  const q = QF[S.cf] + (S.cat ? (QF[S.cf] ? '&' : '') + 'category=' + encodeURIComponent(S.cat) : '');
  await meta(); const list = await cmpList(q);
  render(shell(`<h1>Complaints</h1><div class="chips">${Object.keys(QF).map(k => `<button class="chip" data-act="cf" data-val="${k}" aria-pressed="${k === S.cf}">${QL[k]}</button>`).join('')}</div><label for="cc">Category</label><select id="cc" data-act-change="cat-filter">${opts([['', 'All categories'], ...S.meta.categories], S.cat)}</select>${list}`));
  $('#cc').addEventListener('change', e => { S.cat = e.target.value; show(); });
}
async function food() {
  const F = {open: 'open=1', day: 'recent_hours=24', all: ''}, L = {open: 'Open', day: 'Last 24 hours', all: 'All'};
  const list = await cmpList('category=Food' + (F[S.foodf] ? '&' + F[S.foodf] : ''));
  render(shell(`<h1>Food complaints</h1><div class="chips">${Object.keys(F).map(k => `<button class="chip" data-act="ff" data-val="${k}" aria-pressed="${k === S.foodf}">${L[k]}</button>`).join('')}</div>${list}<p class="muted small">Complaints still marked “Needs review” have no category yet, so they are not listed here.</p>`));
}
/* ---------------- Guests & leave ---------------- */
async function guest() {
  const d = await api('/api/owner/guests'), P = d.guests.filter(g => g.status === 'pending'), A = d.guests.filter(g => g.status === 'approved'), why = {over_limit: `longer than the ${d.max_nights}-night limit`, limit_not_set: 'no guest limit set yet', policy: 'you require approval for all guests'};
  render(shell(`<h1>Guests</h1><h2>Needs your approval</h2>${P.length ? P.map(g => `<div class="card"><b>${esc(g.guest)}</b> for ${esc(g.tenant)} (Room ${esc(g.room)})<br>${g.arrival} → ${g.departure} · ${g.nights} night(s)${g.eats_food ? ' · eats PG food' : ''}<div class="muted small">Why you are asked: ${esc(why[g.reason] || '')}</div>${g.suggested ? `<div class="small">Estimated charge: ${esc(g.suggested.text)}</div>` : ''}<button class="btn sm ok" data-act="g-yes" data-id="${g.id}">Approve</button><button class="btn sm bad" data-act="g-no" data-id="${g.id}">Reject</button></div>`).join('') : '<p class="muted">No requests waiting.</p>'}
  <h2>Approved guests</h2>${A.length ? A.map(g => `<div class="card"><b>${esc(g.guest)}</b> — ${esc(g.tenant)} (Room ${esc(g.room)})<br>${g.arrival} → ${g.departure}</div>`).join('') : '<p class="muted">None.</p>'}
  <h2>Residents away</h2>${d.leaves.length ? d.leaves.map(l => `<div class="card"><b>${esc(l.tenant)}</b> (Room ${esc(l.room)})<br>${esc(l.text)}${l.away_today ? ' <span class="pill warn">Away today</span>' : ''}</div>`).join('') : '<p class="muted">Nobody has told you they are away.</p>'}<p class="muted small">Leave never changes rent or food on its own.</p>`));
}
/* ---------------- Reports ---------------- */
async function rep() {
  const days = S.days || 7, r = await api('/api/owner/report/' + days);
  render(shell(`<h1>Report</h1><div class="chips">${[[1, 'Today'], [7, '7 days'], [30, '30 days']].map(([v, l]) => `<button class="chip" data-act="days" data-val="${v}" aria-pressed="${v === days}">${l}</button>`).join('')}</div>
  <div class="stats"><div class="stat"><div class="sv">${r.complaints_raised}</div><div class="sl">Complaints raised</div></div><div class="stat"><div class="sv">${r.complaints_resolved}</div><div class="sl">Resolved</div></div><div class="stat"><div class="sv">${r.open_now}</div><div class="sl">Open now</div></div><div class="stat"><div class="sv">${r.avg_resolution_hours ?? '—'}</div><div class="sl">Avg hours to resolve</div></div><div class="stat"><div class="sv">${r.food_complaints}</div><div class="sl">Food complaints</div></div></div>
  <h2>Rent this month (${esc(r.month)})</h2><div class="card">${kv('Charged', r.rent_charged.text)}${kv('Received', r.rent_received.text)}${kv('Collected', r.collection_percent === null ? '—' : r.collection_percent + '%')}</div><h2>Beds</h2><div class="card">${kv('Occupied', r.occupied + ' of ' + r.total_beds)}${kv('Vacant', r.vacant)}</div>
  <h2>Repeated problems</h2>${r.repeat_problems.length ? r.repeat_problems.map(x => `<div class="card">${esc(x)}</div>`).join('') : '<p class="muted">No repeated problems.</p>'}<p class="muted small">These are plain counts from your records. AI-written summaries come later.</p>`));
}
/* ---------------- Settings ---------------- */
const fld = (id, label, v = '', type = 'text', extra = '') => `<label for="${id}">${label}</label><input id="${id}" type="${type}" value="${esc(v ?? '')}" ${extra}>`;
async function set() {
  const d = await api('/api/owner/settings'), x = d.details, lf = d.late_fee;
  render(shell(`<h1>Settings</h1>${d.setup.complete ? '<div class="card">✓ Setup complete.</div>' : `<div class="notice"><b>Still to do</b><br>${d.setup.todo.map(esc).join('<br>')}</div>`}
  <details ${d.setup.complete ? '' : 'open'}><summary>PG details and rules</summary>${fld('s_address', 'Address', x.address)}${fld('s_phone', 'PG contact number', x.phone, 'tel')}${fld('s_due', 'Rent due day of month (1–28)', x.rent_due_day, 'number', 'min="1" max="28"')}${fld('s_guest', 'Guest limit: maximum nights (empty = every overnight guest needs your approval)', x.guest_max_nights, 'number', 'min="0"')}
  <label for="s_rules">Basic rules</label><textarea id="s_rules" class="short">${esc(x.basic_rules || '')}</textarea><label for="s_grules">Guest rules (shown to residents)</label><textarea id="s_grules" class="short">${esc(x.guest_rules || '')}</textarea><label for="s_meals">Meal information</label><textarea id="s_meals" class="short">${esc(x.meal_info || '')}</textarea><label for="s_rent">Rent rules</label><textarea id="s_rent" class="short">${esc(x.rent_rules || '')}</textarea><button class="btn" data-act="save-details">Save</button></details>
  <details ${d.floors ? '' : 'open'}><summary>Floors and rooms (${d.floors} floors, ${d.rooms} rooms)</summary>${fld('f_count', 'Number of floors (ground floor counts as 1)', d.floors || '', 'number', 'min="1" max="30"')}<button class="btn sm alt" data-act="floors">Set floors</button>
  <h2>Add a room</h2><div class="two"><div>${fld('r_floor', 'Floor number (0 = ground)', '', 'number', 'min="0"')}</div><div>${fld('r_no', 'Room number')}</div></div><label for="r_cap">Sharing</label><select id="r_cap"><option value="1">Single (1 bed)</option><option value="2">Double (2 beds)</option></select><div class="two"><div>${fld('r_rent', 'Rent per bed (₹/month)', '', 'number', 'min="0"')}</div><div>${fld('r_dep', 'Deposit per bed (₹)', '', 'number', 'min="0"')}</div></div><button class="btn" data-act="add-room">Add room</button></details>
  <details><summary>Staff contacts (${d.staff.length})</summary>${d.staff.map(s => `<div class="kv"><span>${esc(s.name)} · ${esc(s.role)}</span><b>${esc(s.phone)} (${s.language === 'hi' ? 'Hindi' : 'English'})</b></div>`).join('')}<label for="st_role">Role</label><select id="st_role">${opts(['cook', 'cleaner', 'maintenance', 'electrician', 'plumber', 'other'])}</select>${fld('st_name', 'Name')}${fld('st_phone', 'Phone', '', 'tel')}<label for="st_lang">Language for messages</label><select id="st_lang"><option value="hi">Hindi</option><option value="en">English</option></select><button class="btn" data-act="add-staff">Add contact</button><p class="muted small">Staff do not use the app. Messages need SMS/WhatsApp to be connected first.</p></details>
  <details><summary>Backup contact ${x.backup_phone ? '✓' : ''}</summary><p class="muted small">Gets urgent escalations if you do not respond.</p>${fld('b_name', 'Name', x.backup_name)}${fld('b_phone', 'Phone', x.backup_phone, 'tel')}<button class="btn" data-act="backup">Save</button></details>
  <details><summary>Guests and late fee</summary><label><input type="checkbox" id="g_all" ${d.guest_policy.always_require_approval ? 'checked' : ''} class="chk"> Require my approval for every guest</label><button class="btn sm alt" data-act="gpolicy">Save guest setting</button>
  <h2>Late fee ${lf && lf.enabled ? '(ON)' : '(off)'}</h2><p class="muted small">Never charged unless you turn it on here.</p>${lf && lf.enabled ? `<p>${lf.amount.text} once, after ${lf.grace_days} day(s) past the due date.</p><button class="btn sm bad" data-act="lf-off">Turn off</button>` : `${fld('l_amt', 'Fee amount (₹)', '', 'number', 'min="1"')}${fld('l_days', 'Grace days after due date', '3', 'number', 'min="0"')}<button class="btn" data-act="lf-on">Turn on late fee</button>`}</details>
  <details><summary>Notices (${d.notices.length})</summary>${d.notices.map(n => `<div class="card"><b>${esc(n.title)}</b><br>${esc(n.body)}<br><button class="btn sm alt" data-act="n-del" data-id="${n.id}">Remove</button></div>`).join('')}${fld('n_title', 'Title')}<label for="n_body">Message</label><textarea id="n_body" class="short"></textarea><label><input type="checkbox" id="n_pin" class="chk"> Pin to the top</label><button class="btn" data-act="notice">Post notice</button></details>`));
}
const VIEW = {dash, res: () => res(), rooms, pay, cmp, food, guest, rep, set};

/* ---------------- actions ---------------- */
const num = id => val(id) === '' ? null : val(id);
const ACT = {
  login: async () => { try { await api('/api/login/owner', {email: val('em'), password: val('pw')}); boot(); } catch (e) { login(e.message); } },
  logout: async () => { try { await api('/api/logout', {}); } catch (e) {} login(); },
  tab: a => { S.tab = a.dataset.tab; S.open = null; show(); },
  goto: a => { const k = a.dataset.kind; if (k === 'needs_review') S.cf = 'review'; else if (GO[k] === 'cmp') S.cf = 'open'; if (GO[k]) { S.tab = GO[k]; show(); } },
  copy: async a => { try { await navigator.clipboard.writeText(a.dataset.text); toast('Link copied'); } catch (e) { toast('Press and hold the link to copy it', true); } },
  'res-add-form': () => { $('#f').innerHTML = `<div class="card">${fld('n_name', 'Full name')}${fld('n_phone', 'Mobile number', '', 'tel')}${fld('n_email', 'Email (optional)', '', 'email')}${fld('n_food', 'Food plan (optional)')}<button class="btn" data-act="res-add">Add and get invite link</button></div>`; },
  'res-add': async () => { const r = await act('/api/owner/residents', {name: val('n_name'), phone: val('n_phone'), email: val('n_email'), food_plan: val('n_food')}, null, async r => { await res(linkPanel(r.invite_path, val('n_name'))); }); },
  'res-open': a => resProfile(+a.dataset.id),
  invite: async a => { await act(`/api/owner/residents/${a.dataset.id}/invite`, {}, null, async r => { await resProfile(+a.dataset.id, linkPanel(r.invite_path, $('h1').textContent)); }); },
  assign: a => act(`/api/owner/residents/${a.dataset.id}/assign`, {bed_id: val('bed'), move_in: val('mi')}, 'Bed assigned', () => resProfile(+a.dataset.id)),
  move: a => act(`/api/owner/residents/${a.dataset.id}/move`, {bed_id: val('bed2'), new_rent: num('nr')}, null, async r => { await resProfile(+a.dataset.id); toast(r.warning || 'Moved. History is kept.'); }),
  'pay-save': a => act('/api/owner/payments', {tenant_id: +a.dataset.id, amount: val('am'), method: val('me'), received_date: val('pd'), purpose: val('pu'), idem_key: a.dataset.key}, 'Payment saved', () => resProfile(+a.dataset.id)),
  reverse: a => { const reason = prompt('Why is this entry wrong? (required)'); if (reason) act(`/api/owner/payments/${a.dataset.id}/reverse`, {reason}, 'Entry cancelled', () => resProfile(S.rid)); },
  void: a => { const reason = prompt('Why cancel this charge? (required)'); if (reason) act(`/api/owner/charges/${a.dataset.id}/void`, {reason}, 'Charge cancelled', () => resProfile(S.rid)); },
  'mo-prepare': async a => { try { const p = await api(`/api/owner/residents/${a.dataset.id}/moveout/prepare`, {date: val('md'), deductions: [{label: val('dl'), amount: val('da')}]});
      $('#mo').innerHTML = `<div class="card">${kv('Deposit held', p.deposit)}${kv('Unpaid rent and dues', p.dues)}${p.deductions.map(d => kv(esc(d.label), d.amount)).join('')}${kv(p.tenant_owes ? 'Resident still owes' : 'Refund to resident', `<span>${esc(p.refund)}</span>`)}<label for="rf">Type ${p.refund_number} to confirm these figures</label><input id="rf" type="number" step="0.01"><button class="btn bad" data-act="mo-confirm" data-id="${p.move_out_id}">Confirm move-out</button><p class="muted small">This frees the bed and closes the resident's login. It records the refund figure; it does not pay anyone.</p></div>`; } catch (e) { toast(e.message, true); } },
  'mo-confirm': a => act(`/api/owner/moveout/${a.dataset.id}/confirm`, {refund: val('rf')}, 'Move-out confirmed', () => { S.tab = 'res'; show(); }),
  gen: () => act('/api/owner/charges/generate', {period: val('pm')}, null, async r => { toast(r.created + ' rent charge(s) created'); await pay(); }),
  cf: a => { S.cf = a.dataset.val; show(); }, ff: a => { S.foodf = a.dataset.val; show(); }, days: a => { S.days = +a.dataset.val; show(); },
  manage: a => { S.open = S.open === +a.dataset.id ? null : +a.dataset.id; show(); },
  cat: a => act(`/api/owner/complaints/${a.dataset.id}/correct`, {category: a.dataset.val}, 'Category updated'),
  prio: a => act(`/api/owner/complaints/${a.dataset.id}/correct`, {category: a.dataset.cat, priority: a.dataset.val}, 'Priority updated'),
  'assign-c': a => val('st' + a.dataset.id) ? act(`/api/owner/complaints/${a.dataset.id}/assign`, {staff_id: val('st' + a.dataset.id)}, 'Assigned') : toast('Choose a person first', true),
  cstatus: a => act(`/api/owner/complaints/${a.dataset.id}/status`, {status: a.dataset.val}, 'Updated'),
  merge: a => confirm(`Merge #${a.dataset.id} into #${a.dataset.into}? Both residents will still be asked if it is fixed.`) && act(`/api/owner/complaints/${a.dataset.id}/merge`, {into_id: +a.dataset.into}, 'Merged'),
  'g-yes': a => { const ch = prompt('Charge for this guest in ₹? Leave empty for no charge.'); if (ch !== null) act(`/api/owner/guests/${a.dataset.id}/decide`, {approve: true, charge: ch || null}, 'Approved'); },
  'g-no': a => { const note = prompt('Reason for the resident (required)'); if (note) act(`/api/owner/guests/${a.dataset.id}/decide`, {approve: false, note}, 'Rejected'); },
  'save-details': () => act('/api/owner/settings', {address: val('s_address'), phone: val('s_phone'), rent_due_day: val('s_due'), guest_max_nights: val('s_guest'), basic_rules: val('s_rules'), guest_rules: val('s_grules'), meal_info: val('s_meals'), rent_rules: val('s_rent')}, 'Saved'),
  floors: () => act('/api/owner/floors', {count: val('f_count')}, 'Floors saved'),
  'add-room': () => act('/api/owner/rooms', {floor: val('r_floor'), room_number: val('r_no'), capacity: val('r_cap'), rent: val('r_rent'), deposit: val('r_dep')}, 'Room added'),
  'add-staff': () => act('/api/owner/staff', {role: val('st_role'), name: val('st_name'), phone: val('st_phone'), language: val('st_lang')}, 'Contact added'),
  backup: () => act('/api/owner/backup-contact', {name: val('b_name'), phone: val('b_phone')}, 'Saved'),
  gpolicy: () => act('/api/owner/guest-policy', {always_require_approval: $('#g_all').checked}, 'Saved'),
  'lf-on': () => act('/api/owner/late-fee', {amount: val('l_amt'), grace_days: val('l_days')}, 'Late fee turned on'), 'lf-off': () => act('/api/owner/late-fee/disable', {}, 'Late fee turned off'),
  notice: () => act('/api/owner/notices', {title: val('n_title'), body: val('n_body'), pinned: $('#n_pin').checked}, 'Notice posted'),
  'n-del': a => act(`/api/owner/notices/${a.dataset.id}/archive`, {}, 'Removed')
};
app.addEventListener('click', e => { const a = e.target.closest('[data-act]'); if (a && ACT[a.dataset.act]) ACT[a.dataset.act](a); });
async function boot() {
  let me; try { me = await api('/api/me'); } catch (e) { return e.status === 401 ? login() : render(`<p class="pad">No connection. <button class="btn" data-act="tab" data-tab="dash">Retry</button></p>`); }
  if (me.role !== 'owner') return login('This page is for owners. Log in with your owner email.');
  S.pg = me.pg; show();
}
boot();
