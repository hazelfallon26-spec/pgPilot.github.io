'use strict';
const $ = s => document.querySelector(s), app = $('#app');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(16).slice(2));
const today = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); };
const fmt = iso => iso ? new Date(iso + 'T00:00').toLocaleDateString('en-IN', {day: 'numeric', month: 'short'}) : '';

/* ---------- language (English / Hindi). Messages that come from the server stay in English for now ---------- */
const T = {en: {rent:'Rent & Payments', problem:'Report a Problem', food:'Food Complaint', clean:'Cleanliness', leave:'Going Home / Leave', guest:'Guest / Visitor', notices:'Notices', help:'AI Help',
  home:'Home', back:'Back', send:'Send', login:'Log in', phone:'Mobile number', pin:'PIN', paid:'Paid', remaining:'Remaining', due:'Due', rentLbl:'Monthly rent', none:'Nothing here yet.', hello:'Hello',
  describe:'Describe the problem', about:'What is it about? (optional)', meal:'Which meal?', issue:'What was wrong?', area:'Where?', note:'Anything else? (optional)', mine:'My problems', fixedQ:'Is it fixed?', yes:'Yes, fixed', no:'No, still a problem',
  leaveDate:'Leaving on', returnDate:'Coming back on (optional)', reason:'Reason (optional)', cancel:'Cancel', guestName:'Guest name', rel:'Relationship (optional)', arrive:'Arriving on', depart:'Leaving on', overnight:'Staying overnight?', eats:'Will eat PG food?',
  yesS:'Yes', noS:'No', askQ:'Ask a question', ask:'Ask', confirmSend:'Confirm and send', messages:'Messages', logout:'Log out', setPin:'Choose a PIN (4–6 digits)', setPin2:'Type the PIN again', start:'Start', paidAll:'Paid', overdue:'Overdue', deposit:'Security deposit', history:'Payment history', total:'Total pending'},
 hi: {rent:'किराया और भुगतान', problem:'समस्या बताएं', food:'खाने की शिकायत', clean:'सफाई', leave:'घर जाना / छुट्टी', guest:'मेहमान', notices:'सूचनाएं', help:'AI मदद',
  home:'होम', back:'वापस', send:'भेजें', login:'लॉग इन', phone:'मोबाइल नंबर', pin:'पिन', paid:'चुकाया', remaining:'बाकी', due:'अंतिम तारीख', rentLbl:'महीने का किराया', none:'अभी कुछ नहीं है।', hello:'नमस्ते',
  describe:'समस्या बताइए', about:'यह किस बारे में है? (ज़रूरी नहीं)', meal:'कौन सा खाना?', issue:'क्या दिक्कत थी?', area:'कहाँ?', note:'और कुछ? (ज़रूरी नहीं)', mine:'मेरी शिकायतें', fixedQ:'क्या ठीक हो गया?', yes:'हाँ, ठीक हुआ', no:'नहीं, अभी भी दिक्कत है',
  leaveDate:'जाने की तारीख', returnDate:'वापसी की तारीख (ज़रूरी नहीं)', reason:'कारण (ज़रूरी नहीं)', cancel:'रद्द करें', guestName:'मेहमान का नाम', rel:'रिश्ता (ज़रूरी नहीं)', arrive:'आने की तारीख', depart:'जाने की तारीख', overnight:'रात रुकेंगे?', eats:'PG का खाना खाएंगे?',
  yesS:'हाँ', noS:'नहीं', askQ:'सवाल पूछें', ask:'पूछें', confirmSend:'पक्का करके भेजें', messages:'संदेश', logout:'लॉग आउट', setPin:'पिन चुनें (4–6 अंक)', setPin2:'पिन दोबारा लिखें', start:'शुरू करें', paidAll:'चुकाया', overdue:'देर हो गई', deposit:'सिक्योरिटी डिपॉज़िट', history:'भुगतान का इतिहास', total:'कुल बाकी'}};
let lang = localStorage.getItem('lang') || 'en';
const t = k => (T[lang] && T[lang][k]) || T.en[k] || k;
const OPT = {en: {Maintenance:'Repairs', Plumbing:'Water pipe / tap', Electricity:'Electricity', 'AC/Fan':'AC / Fan', Water:'No water', 'Wi-Fi/Internet':'Wi-Fi', 'Room/Furniture':'Furniture', Security:'Security', Noise:'Noise', Other:'Other',
  Breakfast:'Breakfast', Lunch:'Lunch', Dinner:'Dinner', yes:'Yes', no:'No', 'Poor quality':'Poor quality', 'Too spicy':'Too spicy', 'Too oily':'Too oily', 'Not fresh':'Not fresh', 'Not available':'Not available', 'Too little':'Too little', 'Late':'Late', Bathroom:'Bathroom', Room:'Room', Corridor:'Corridor', 'Dining/Kitchen':'Dining / Kitchen'},
 hi: {Maintenance:'मरम्मत', Plumbing:'पाइप / नल', Electricity:'बिजली', 'AC/Fan':'AC / पंखा', Water:'पानी नहीं', 'Wi-Fi/Internet':'वाई-फाई', 'Room/Furniture':'फर्नीचर', Security:'सुरक्षा', Noise:'शोर', Other:'अन्य',
  Breakfast:'नाश्ता', Lunch:'दोपहर का खाना', Dinner:'रात का खाना', yes:'हाँ', no:'नहीं', 'Poor quality':'खराब क्वालिटी', 'Too spicy':'बहुत तीखा', 'Too oily':'बहुत तैलीय', 'Not fresh':'ताज़ा नहीं', 'Not available':'खाना नहीं मिला', 'Too little':'कम मात्रा', 'Late':'देर से', Bathroom:'बाथरूम', Room:'कमरा', Corridor:'गलियारा', 'Dining/Kitchen':'डाइनिंग / रसोई'}};
const o = k => (OPT[lang] && OPT[lang][k]) || k;

/* ---------- talking to the server; actions that fail for lack of internet are kept and re-sent (each carries a unique key, so nothing is ever recorded twice) ---------- */
async function api(path, body) {
  const opt = {method: body === undefined ? 'GET' : 'POST', headers: {'X-PG': '1'}};
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt); let d = {}; try { d = await r.json(); } catch (e) {}
  if (!r.ok) { const e = new Error(d.error || 'Something went wrong. Please try again.'); e.status = r.status; throw e; }
  return d;
}
const getOB = () => { try { return JSON.parse(localStorage.getItem('outbox') || '[]'); } catch (e) { return []; } }, setOB = v => { localStorage.setItem('outbox', JSON.stringify(v)); showBanner(); };
async function send(path, body) {
  try { return {ok: true, data: await api(path, body)}; }
  catch (e) { if (e.status) return {ok: false, error: e.message}; setOB([...getOB(), {path, body}]); return {ok: false, queued: true}; }
}
async function flush() {
  const keep = []; for (const it of getOB()) { try { await api(it.path, it.body); } catch (e) { if (!e.status || e.status === 401) keep.push(it); else toast('Could not send: ' + e.message, true); } }
  setOB(keep);
}
function showBanner() { const n = getOB().length, b = $('#banner'); b.hidden = !n; b.textContent = n ? `${n} saved on your phone — will send when you are back online` : ''; }
let tt; function toast(m, bad) { const e = $('#toast'); e.textContent = m; e.className = bad ? 'bad' : ''; e.hidden = false; clearTimeout(tt); tt = setTimeout(() => e.hidden = true, 5000); }
const draft = k => ({get: () => localStorage.getItem('draft:' + k) || '', set: v => localStorage.setItem('draft:' + k, v), clear: () => localStorage.removeItem('draft:' + k)});

/* ---------- shell ---------- */
function render(html) { app.innerHTML = html; app.focus({preventScroll: true}); window.scrollTo(0, 0); }
const bar = (title, back = true) => `<div class="bar">${back ? `<button class="ghost" data-act="go" data-to="home" aria-label="${t('back')}">←</button>` : ''}<div class="grow">${esc(title)}</div><button class="ghost" data-act="lang">${lang === 'en' ? 'हिंदी' : 'English'}</button></div>`;
const chips = (g, arr, cls = '') => `<div class="chips" data-group="${g}">${arr.map(v => `<button type="button" class="chip ${cls}" data-act="chip" data-val="${esc(v)}" aria-pressed="false">${esc(o(v))}</button>`).join('')}</div>`;
const picked = g => { const e = app.querySelector(`[data-group="${g}"] [aria-pressed="true"]`); return e ? e.dataset.val : ''; };
const steps = st => { const i = ['Reported', 'Being handled', 'Resolved - please confirm', 'Confirmed'].indexOf(st); return `<div class="steps" aria-label="${esc(st)}">${['Reported', 'Being handled', 'Resolved', 'Confirmed'].map((s, k) => `<span class="${k < i || i === 3 ? 'done' : k === i ? 'on' : ''}">${k < i || i === 3 ? '✓ ' : ''}${s}</span>`).join('')}</div>`; };
let user = null, cache = {};

/* ---------- views ---------- */
async function loginView() {
  render(`${bar('My PG', false)}<h1>${t('login')}</h1><label for="ph">${t('phone')}</label><input id="ph" type="tel" inputmode="numeric" autocomplete="username" maxlength="14">
  <label for="pn">${t('pin')}</label><input id="pn" type="password" inputmode="numeric" autocomplete="current-password" maxlength="6"><button class="btn" data-act="login">${t('login')}</button>
  <p class="muted small">New here? Open the invite link your PG owner sent you.</p>`);
}
async function joinView() {
  const tok = location.pathname.split('/')[2]; let info;
  try { info = await api('/api/invite/' + encodeURIComponent(tok)); } catch (e) { return render(`${bar('My PG', false)}<h1>Invite problem</h1><p>${esc(e.message)}. Ask your PG owner to send a new link.</p>`); }
  render(`${bar(info.pg, false)}<h1>${t('hello')} ${esc(info.name)}</h1><p class="muted">Choose a PIN. You will use it with your mobile number to log in.</p><label for="p1">${t('setPin')}</label><input id="p1" type="password" inputmode="numeric" maxlength="6" autocomplete="new-password">
  <label for="p2">${t('setPin2')}</label><input id="p2" type="password" inputmode="numeric" maxlength="6" autocomplete="new-password"><button class="btn" data-act="join" data-tok="${esc(tok)}">${t('start')}</button>`);
}
async function homeView() {
  const h = cache.home = await api('/api/tenant/home'), r = h.rent;
  let pill = '', rentHtml = `<div class="big">—</div><div>No rent has been charged to you yet.</div>`;
  if (r) { const late = r.remaining.paise > 0 && r.due_date < today();
    pill = r.remaining.paise === 0 ? `<span class="pill ok">✓ ${t('paidAll')}</span>` : late ? `<span class="pill bad">⚠ ${t('overdue')}</span>` : `<span class="pill warn">${t('due')} ${esc(r.due_text)}</span>`;
    rentHtml = `<div>${t('rentLbl')}</div><div class="big">${esc(r.amount.text)}</div>${pill}<div class="row"><div>${t('paid')}<b>${esc(r.paid.text)}</b></div><div>${t('remaining')}<b>${esc(r.remaining.text)}</b></div><div>${t('due')}<b>${esc(r.due_text)}</b></div></div>`; }
  const T8 = [['rent', '💰', 'rent'], ['report', '🛠️', 'problem'], ['food', '🍽️', 'food'], ['clean', '🧹', 'clean'], ['leave', '🏠', 'leave'], ['guest', '👥', 'guest'], ['notices', '📢', 'notices'], ['help', '💬', 'help']];
  render(`${bar(h.pg, false)}<h1>${t('hello')}, ${esc(h.name.split(' ')[0])}</h1><p class="muted">Room ${esc(h.room || '—')} · Bed ${esc(h.bed || '—')}</p>
  <button class="rent rent-btn" data-act="go" data-to="rent">${rentHtml}</button>
  ${h.needs_confirmation ? `<button class="notice" data-act="go" data-to="mine"><b>${t('fixedQ')}</b> ${h.needs_confirmation} problem(s) marked fixed — please confirm.</button>` : ''}
  ${h.unread ? `<button class="notice" data-act="go" data-to="inbox"><b>${h.unread}</b> new message(s)</button>` : ''}
  <div class="tiles mt">${T8.map(([to, ic, k]) => `<button class="tile" data-act="go" data-to="${to}"><span class="ic" aria-hidden="true">${ic}</span>${t(k)}</button>`).join('')}</div>
  <button class="btn alt" data-act="go" data-to="mine">${t('mine')}${h.open_complaints ? ' (' + h.open_complaints + ')' : ''}</button><button class="btn alt" data-act="logout">${t('logout')}</button>`);
}
async function rentView() {
  const d = await api('/api/tenant/rent'), r = d.rent;
  render(`${bar(t('rent'))}${r ? `<div class="card"><div class="muted">${t('rentLbl')}</div><h1>${esc(r.amount.text)}</h1><p>${t('paid')}: <b>${esc(r.paid.text)}</b><br>${t('remaining')}: <b>${esc(r.remaining.text)}</b><br>${t('due')}: <b>${esc(r.due_text)}</b></p></div>` : `<p>No rent has been charged to you yet.</p>`}
  ${d.total_pending.paise ? `<p>${t('total')}: <b>${esc(d.total_pending.text)}</b></p>` : ''}${d.advance_credit.paise ? `<p>Advance paid: <b>${esc(d.advance_credit.text)}</b></p>` : ''}
  <h2>${t('deposit')}</h2><div class="card">${esc(d.deposit.paid.text)} of ${esc(d.deposit.expected.text)} — ${esc(d.deposit.status)}</div>
  <h2>${t('history')}</h2>${d.history.length ? d.history.map(x => `<div class="card">${esc(fmt(x.date))} — ${esc(x.amount.text)} ${x.type === 'reversal' ? '(cancelled entry)' : '(' + esc(x.method) + ')'}</div>`).join('') : `<p class="muted">${t('none')}</p>`}`);
}
const KINDS = {general: {title: 'problem', hint: ''}, food: {title: 'food', hint: 'Food'}, clean: {title: 'clean', hint: 'Cleanliness'}};
function reportView(kind) {
  const d = draft(kind); let form = '';
  if (kind === 'general') form = `<label for="tx">${t('describe')}</label><textarea id="tx" maxlength="2000" autocomplete="off">${esc(d.get())}</textarea><label>${t('about')}</label>${chips('cat', ['Maintenance', 'Plumbing', 'Electricity', 'AC/Fan', 'Water', 'Wi-Fi/Internet', 'Room/Furniture', 'Security', 'Noise', 'Other'])}`;
  if (kind === 'food') form = `<label>${t('meal')}</label>${chips('meal', ['Breakfast', 'Lunch', 'Dinner'])}<label>${t('issue')}</label>${chips('issue', ['Poor quality', 'Too spicy', 'Too oily', 'Not fresh', 'Not available', 'Too little', 'Late', 'Other'])}<label for="tx">${t('note')}</label><textarea id="tx" maxlength="1000" class="short">${esc(d.get())}</textarea>`;
  if (kind === 'clean') form = `<label>${t('area')}</label>${chips('area', ['Bathroom', 'Room', 'Corridor', 'Dining/Kitchen', 'Other'])}<label for="tx">${t('note')}</label><textarea id="tx" maxlength="1000" class="short">${esc(d.get())}</textarea>`;
  render(`${bar(t(KINDS[kind].title))}${form}<button class="btn" data-act="report" data-kind="${kind}">${t('send')}</button>`);
  const tx = $('#tx'); if (tx) tx.addEventListener('input', () => d.set(tx.value));
}
async function submitReport(kind) {
  const note = ($('#tx') || {}).value || ''; let text = note.trim(), hint = KINDS[kind].hint;
  if (kind === 'general') { hint = picked('cat'); if (text.length < 3) return toast(t('describe'), true); }
  if (kind === 'food') { const m = picked('meal'), i = picked('issue'); if (!m || !i) return toast(`${t('meal')} / ${t('issue')}`, true); text = `Food complaint — ${m}: ${i}.${note.trim() ? ' ' + note.trim() : ''}`; }
  if (kind === 'clean') { const a = picked('area'); if (!a) return toast(t('area'), true); text = `Cleanliness — ${a}.${note.trim() ? ' ' + note.trim() : ''}`; }
  const btn = app.querySelector('[data-act=report]'); btn.disabled = true;
  const r = await send('/api/tenant/complaints', {text, hint: hint || null, idem_key: uid()}); btn.disabled = false;
  if (r.ok || r.queued) draft(kind).clear();
  render(`${bar(t('problem'))}<h1>${r.ok ? '✓ Reported' : r.queued ? 'Saved on your phone' : 'Could not send'}</h1><p>${r.ok ? 'The PG has been told. You can follow progress under “My problems”.' : r.queued ? 'No internet right now. It will be sent automatically when you are back online.' : esc(r.error)}</p><button class="btn" data-act="go" data-to="mine">${t('mine')}</button><button class="btn alt" data-act="go" data-to="home">${t('home')}</button>`);
}
async function mineView() {
  const d = await api('/api/tenant/complaints');
  render(`${bar(t('mine'))}${d.complaints.length ? d.complaints.map(c => `<div class="card"><div>${esc(c.text)}</div><div class="muted small">${esc(c.category || '')}</div>${steps(c.status)}${c.needs_my_confirmation ? `<b>${t('fixedQ')}</b><div class="row2"><button class="btn ok" data-act="confirm" data-id="${c.id}" data-fixed="1">${t('yes')}</button><button class="btn bad" data-act="confirm" data-id="${c.id}" data-fixed="0">${t('no')}</button></div>` : ''}</div>`).join('') : `<p class="muted">${t('none')}</p>`}`);
}
async function leaveView() {
  const d = await api('/api/tenant/leaves'), k = uid();
  render(`${bar(t('leave'))}<label for="ld">${t('leaveDate')}</label><input id="ld" type="date" min="${today()}" value="${today()}"><label for="rd">${t('returnDate')}</label><input id="rd" type="date" min="${today()}"><label for="rs">${t('reason')}</label><input id="rs" maxlength="200">
  <p class="muted small">Your rent and food are not changed by this. The PG owner decides that.</p><button class="btn" data-act="leave" data-key="${k}">${t('send')}</button>
  <h2>${t('leave')}</h2>${d.leaves.length ? d.leaves.map(l => `<div class="card">${esc(fmt(l.leave_date))} → ${l.return_date ? esc(fmt(l.return_date)) : '?'} <span class="pill ${l.status === 'active' ? 'warn' : 'ok'}">${esc(l.status.replace('_', ' '))}</span>${l.status === 'active' ? `<button class="btn alt" data-act="cancel-leave" data-id="${l.id}">${t('cancel')}</button>` : ''}</div>`).join('') : `<p class="muted">${t('none')}</p>`}`);
}
async function guestView() {
  const d = await api('/api/tenant/guests'), k = uid(), r = d.rules;
  render(`${bar(t('guest'))}<div class="notice"><b>Guest rules</b><br>${r.rules_text ? esc(r.rules_text) : 'The PG has not written guest rules yet.'}${r.max_nights != null ? `<br>Maximum stay: ${r.max_nights} night(s).` : '<br>Overnight stays need the owner’s approval.'}</div>
  <label for="gn">${t('guestName')}</label><input id="gn" maxlength="80"><label for="gr">${t('rel')}</label><input id="gr" maxlength="60"><div class="row2"><div><label for="ga">${t('arrive')}</label><input id="ga" type="date" min="${today()}" value="${today()}"></div><div><label for="gd">${t('depart')}</label><input id="gd" type="date" min="${today()}" value="${today()}"></div></div>
  <label>${t('overnight')}</label>${chips('night', ['yes', 'no'])}<label>${t('eats')}</label>${chips('eat', ['yes', 'no'])}<button class="btn" data-act="guest" data-key="${k}">${t('send')}</button>
  <h2>${t('guest')}</h2>${d.guests.length ? d.guests.map(g => `<div class="card"><b>${esc(g.guest_name)}</b> — ${esc(fmt(g.arrival))} → ${esc(fmt(g.departure))}<br><span class="pill ${g.status === 'approved' ? 'ok' : g.status === 'pending' ? 'warn' : 'bad'}">${esc(g.status)}</span>${g.decision_note ? `<div class="small">${esc(g.decision_note)}</div>` : ''}${['pending', 'approved'].includes(g.status) ? `<button class="btn alt" data-act="cancel-guest" data-id="${g.id}">${t('cancel')}</button>` : ''}</div>`).join('') : `<p class="muted">${t('none')}</p>`}`);
}
async function noticesView() {
  const d = await api('/api/notices'); render(`${bar(t('notices'))}${d.notices.length ? d.notices.map(n => `<div class="card"><b>${n.pinned ? '📌 ' : ''}${esc(n.title)}</b><p class="pre">${esc(n.body)}</p></div>`).join('') : `<p class="muted">${t('none')}</p>`}`);
}
async function inboxView() {
  const d = await api('/api/tenant/notifications'); render(`${bar(t('messages'))}${d.notifications.length ? d.notifications.map(n => `<div class="card">${esc(n.message)}</div>`).join('') : `<p class="muted">${t('none')}</p>`}`);
  send('/api/tenant/notifications/read', {});
}
function helpView() {
  render(`${bar(t('help'))}<div class="chat" id="chat"><div class="msg">Ask about your rent, deposit, room, or problems. I only answer from your PG’s records.</div></div><label for="aq">${t('askQ')}</label><input id="aq" maxlength="300" autocomplete="off"><button class="btn" data-act="ask">${t('ask')}</button>`);
}
async function ask() {
  const q = $('#aq').value.trim(); if (!q) return; $('#aq').value = ''; const chat = $('#chat'); chat.insertAdjacentHTML('beforeend', `<div class="msg me">${esc(q)}</div>`);
  try { const r = await api('/api/tenant/assistant', {text: q}); let extra = '';
    if (r.action && r.action.type === 'create_complaint') { cache.proposed = r.action.text; extra = `<button class="btn" data-act="send-proposed">${t('confirmSend')}</button>`; }
    if (r.action && r.action.type === 'confirm_fixed') extra = `<button class="btn" data-act="go" data-to="mine">${t('mine')}</button>`;
    chat.insertAdjacentHTML('beforeend', `<div class="msg">${esc(r.answer)}${extra}</div>`); } catch (e) { toast(e.message, true); }
}

/* ---------- actions ---------- */
const VIEWS = {home: homeView, rent: rentView, report: () => reportView('general'), food: () => reportView('food'), clean: () => reportView('clean'), mine: mineView, leave: leaveView, guest: guestView, notices: noticesView, help: helpView, inbox: inboxView};
const go = async to => { try { await (VIEWS[to] || homeView)(); } catch (e) { if (e.status === 401) loginView(); else toast(e.message || 'No connection. Please try again.', true); } };
const val = id => ($('#' + id) || {}).value || '';
const ACT = {
  go: a => go(a.dataset.to),
  lang: () => { lang = lang === 'en' ? 'hi' : 'en'; localStorage.setItem('lang', lang); document.documentElement.lang = lang; boot(); },
  chip: a => { a.parentElement.querySelectorAll('.chip').forEach(c => c.setAttribute('aria-pressed', c === a && a.getAttribute('aria-pressed') !== 'true' ? 'true' : 'false')); },
  login: async () => { try { await api('/api/login/tenant', {phone: val('ph'), pin: val('pn')}); boot(); } catch (e) { toast(e.message, true); } },
  join: async a => { if (val('p1') !== val('p2')) return toast('The two PINs are different', true); try { await api('/api/join', {token: a.dataset.tok, pin: val('p1')}); history.replaceState({}, '', '/'); boot(); } catch (e) { toast(e.message, true); } },
  logout: async () => { try { await api('/api/logout', {}); } catch (e) {} loginView(); },
  report: a => submitReport(a.dataset.kind),
  confirm: async a => { try { await api(`/api/tenant/complaints/${a.dataset.id}/confirm`, {fixed: a.dataset.fixed === '1'}); toast(a.dataset.fixed === '1' ? 'Thank you — closed.' : 'We have reopened it.'); mineView(); } catch (e) { toast(e.message, true); } },
  leave: async a => { const r = await send('/api/tenant/leaves', {leave_date: val('ld'), return_date: val('rd') || null, reason: val('rs') || null, idem_key: a.dataset.key}); r.error ? toast(r.error, true) : (toast(r.queued ? 'Saved — will send when online' : 'Saved'), leaveView()); },
  'cancel-leave': async a => { try { await api(`/api/tenant/leaves/${a.dataset.id}/cancel`, {}); leaveView(); } catch (e) { toast(e.message, true); } },
  guest: async a => { const n = picked('night'), e = picked('eat'); if (!n || !e) return toast(`${t('overnight')} / ${t('eats')}`, true);
    const r = await send('/api/tenant/guests', {guest_name: val('gn'), relationship: val('gr') || null, arrival: val('ga'), departure: val('gd'), overnight: n === 'yes', eats_food: e === 'yes', idem_key: a.dataset.key});
    r.error ? toast(r.error, true) : (toast(r.queued ? 'Saved — will send when online' : (r.data.message || 'Sent')), guestView()); },
  'cancel-guest': async a => { try { await api(`/api/tenant/guests/${a.dataset.id}/cancel`, {}); guestView(); } catch (e) { toast(e.message, true); } },
  ask: ask, retry: () => boot(),
  'send-proposed': async a => { a.disabled = true; const r = await send('/api/tenant/complaints', {text: cache.proposed, hint: null, idem_key: uid()}); toast(r.ok ? 'Reported' : r.queued ? 'Saved — will send when online' : r.error, !r.ok && !r.queued); }
};
app.addEventListener('click', e => { const a = e.target.closest('[data-act]'); if (a && ACT[a.dataset.act]) ACT[a.dataset.act](a); });
app.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.id === 'aq') ACT.ask(); });

async function boot() {
  document.documentElement.lang = lang; showBanner();
  if (location.pathname.startsWith('/join/')) return joinView();
  try { user = await api('/api/me'); } catch (e) { return e.status === 401 ? loginView() : render(`${bar('My PG', false)}<p class="pad">No connection. Please try again.</p><button class="btn" data-act="retry">Retry</button>`); }
  if (user.role !== 'tenant') return render(`${bar('My PG', false)}<h1>Owner login</h1><p>The owner screens are not built yet. Use the tenant login on this page.</p><button class="btn alt" data-act="logout">${t('logout')}</button>`);
  flush().then(() => {}); homeView().catch(e => toast(e.message, true));
}
window.addEventListener('online', () => flush()); boot();
