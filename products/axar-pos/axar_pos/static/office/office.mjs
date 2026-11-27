// AXAR ONE — office screens (#60 P0-2): register reconciliation + sale trace,
// POS settings (offline limit, owner decision: set in the settings screen) and
// the review queue of offline till entries. Everything is decided by the
// server; this module only shows answers and sends the manager's choices.
//
// Server mode: these are back-office actions (owner or tenant-wide manager,
// password + TOTP). The office session token lives in memory only and never
// replaces the till's own session.
import { formatEuro } from '../one/format.mjs';
import { createCatalogOffice } from './office-catalog.mjs';

const STATUS_ET = { ok: 'Korras', warning: 'Ootel', fail: 'VIGA' };
const ENTRY_ET = { sale: 'Müük', refund: 'Tagastus' };

export function createOffice({ api, el, stateBox, announce, ApiError, authMode, tenantId }) {
  const st = { tab: 'recon', token: null, report: null, trace: null, busy: false };
  let root = null;

  const opts = (extra = {}) => ({ ...extra, noLock: true,
    headers: { ...(extra.headers || {}), ...(st.token ? { Authorization: `Bearer ${st.token}` } : {}) } });
  const call = (path, extra) => api(path, opts(extra));
  const needsLogin = () => authMode() === 'server' && !st.token;
  const errText = (e) => {
    if (e instanceof ApiError && (e.code === 'mfa_required' || e.status === 401)) { st.token = null; return 'Vajab kontori sisselogimist (parool + TOTP).'; }
    if (e instanceof ApiError && e.status === 403) return 'Selleks on vaja omanikku või kõigi asukohtade juhti.';
    return e.message;
  };
  function button(text, cls, id, onClick) {
    const b = el('button', cls, text); b.type = 'button'; if (id) b.id = id; b.addEventListener('click', onClick); return b;
  }
  function field(label, input) { const w = el('label', 'inv-field'); w.append(el('span', null, label), input); return w; }
  const catalogUi = createCatalogOffice({ call: (...a) => call(...a), el, stateBox, announce, button, field,
    errText: (e) => errText(e) });

  async function render(container) {
    root = container; root.replaceChildren();
    const tabs = el('div', 'inv-tabs'); tabs.setAttribute('role', 'tablist');
    for (const [k, label] of [['recon', 'Kooskõla'], ['catalog', 'Tooted ja hinnad'], ['settings', 'Kassa seaded'], ['quarantine', 'Võrguühenduseta kirjed']]) {
      const b = button(label, `category-btn${st.tab === k ? ' active' : ''}`, `office-tab-${k}`, () => { st.tab = k; render(root); });
      b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', String(st.tab === k));
      tabs.append(b);
    }
    const body = el('section', 'inv-body'); body.id = 'office-body';
    root.append(tabs, body);
    if (needsLogin()) { renderLogin(body); return; }
    if (st.tab === 'recon') renderRecon(body);
    else if (st.tab === 'catalog') await catalogUi.render(body);
    else if (st.tab === 'settings') await renderSettings(body);
    else await renderQuarantine(body);
  }

  // ------------------------------------------------------------------ login

  function renderLogin(body) {
    body.replaceChildren(el('div', 'section-label', 'Kontori sisselogimine'),
      el('p', 'muted', 'Need toimingud kehtivad kõigis asukohtades: vaja on omanikku või kõigi asukohtade juhti, parooli ja ühekordset koodi.'));
    const login = el('input'); login.id = 'office-login'; login.autocomplete = 'username';
    const pw = el('input'); pw.id = 'office-password'; pw.type = 'password'; pw.autocomplete = 'current-password';
    const totp = el('input'); totp.id = 'office-totp'; totp.inputMode = 'numeric'; totp.maxLength = 6; totp.autocomplete = 'one-time-code';
    const msg = el('div', 'dialog-error'); msg.setAttribute('role', 'alert');
    const go = button('Logi sisse', 'primary-btn', 'office-login-submit', async () => {
      msg.textContent = '';
      try {
        const { data } = await api('/api/v1/auth/login', { method: 'POST', noLock: true, body: {
          tenant_id: tenantId(), login: login.value.trim(), password: pw.value, totp: totp.value.trim() } });
        st.token = data.token; pw.value = ''; totp.value = '';
        announce('Kontori sisselogimine õnnestus'); render(root);
      } catch (e) { msg.textContent = e.message; pw.value = ''; totp.value = ''; }
    });
    const form = el('div', 'inv-form');
    form.append(field('Kasutajanimi', login), field('Parool', pw), field('Kood (TOTP)', totp), go, msg);
    body.append(form);
  }

  // --------------------------------------------------------- reconciliation

  function renderRecon(body) {
    body.replaceChildren();
    const head = el('div', 'inv-head');
    head.append(el('div', 'section-label', 'Registrite kooskõla'),
      button('Kontrolli', 'primary-btn inv-add', 'recon-run', () => runRecon(body)));
    body.append(head, el('p', 'muted', 'Võrdleb kassasahtlit, müüke, makseid, tagastusi, tšekinumbreid, sündmusi, ladu, kööki, arveid ja kassade sünkroonimist. Midagi ei muudeta.'));
    const out = el('div'); out.id = 'recon-out'; body.append(out);
    if (st.report) drawReport(out); else out.append(el('p', 'muted-p', 'Vajuta „Kontrolli“.'));
    body.append(traceBox());
  }

  async function runRecon(body) {
    const out = body.querySelector('#recon-out');
    out.replaceChildren(el('p', 'muted-p', 'Kontrollin…'));
    try { st.report = (await call('/api/v1/admin/reconciliation')).data; drawReport(out); }
    catch (e) { out.replaceChildren(stateBox('error', errText(e))); if (needsLogin()) render(root); }
  }

  function drawReport(out) {
    const r = st.report;
    const verdict = r.ok ? (r.complete ? 'Kõik registrid on kooskõlas.' : 'Vigu pole; mõni töö on veel pooleli (vt „Ootel“).')
      : 'Registrid ei klapi: vaata punaseid ridu.';
    out.replaceChildren(stateBox(r.ok ? 'ok' : 'error', verdict));
    const list = el('ul', 'recon-list'); list.id = 'recon-list';
    for (const c of r.checks) {
      const li = el('li', `recon-row ${c.status}`); li.dataset.check = c.check;
      const top = el('div', 'recon-top');
      top.append(el('span', `recon-badge ${c.status}`, STATUS_ET[c.status]), el('strong', null, c.title),
        el('small', null, `${c.check} · kontrollitud ${c.checked}`));
      li.append(top);
      const rows = c.status === 'fail' ? c.problems : c.status === 'warning' ? c.pending : [];
      if (rows.length) {
        const n = c.status === 'fail' ? c.problem_count : c.pending_count;
        const det = el('details'); det.append(el('summary', null, `${n} ${c.status === 'fail' ? 'viga' : 'ootel'}`));
        const pre = el('pre', 'recon-detail', rows.map((x) => JSON.stringify(x)).join('\n'));
        det.append(pre); li.append(det);
      }
      list.append(li);
    }
    out.append(list);
    announce(verdict);
  }

  function traceBox() {
    const box = el('div', 'recon-trace');
    const input = el('input'); input.id = 'trace-sale-id'; input.placeholder = 'müügi id';
    const out = el('div'); out.id = 'trace-out';
    const go = button('Jälgi müüki', 'secondary-btn', 'trace-run', async () => {
      out.replaceChildren(el('p', 'muted-p', 'Otsin…'));
      try { drawTrace(out, (await call(`/api/v1/admin/trace/sales/${encodeURIComponent(input.value.trim())}`)).data); }
      catch (e) { out.replaceChildren(stateBox('error', e instanceof ApiError && e.status === 404 ? 'Sellist müüki ei leitud.' : errText(e))); }
    });
    box.append(el('div', 'section-label', 'Müügi teekond läbi registrite'), field('Müük', input), go, out);
    return box;
  }

  function drawTrace(out, t) {
    const s = t.sale;
    const items = [
      `Müük ${s.receipt_label || s.receipt_no} · kassa ${s.register} · ${formatEuro(s.total_cents)} · ${s.payment_method}`,
      t.order ? `Tellimus: ${t.order.status}; köögitšekid: ${t.kitchen_tickets.map((k) => `#${k.ticket_no} ${k.status}`).join(', ') || '—'}` : 'Kiirmüük (tellimuseta)',
      `Maksed: ${t.payments.map((p) => `${p.method} ${formatEuro(p.amount_cents)} ${p.status}`).join(', ') || 'sularaha müügil'}`,
      `Tagastused: ${t.refunds.map((r) => `#${r.refund_no} ${formatEuro(r.total_cents)}`).join(', ') || '—'}`,
      `Sündmused: ${t.events.map((e) => `${e.seq} ${e.type}${e.applied_by.length ? ` → ${e.applied_by.map((a) => a.consumer).join(' + ')}` : ''}`).join('; ')}`,
      `Laoliikumised: ${t.stock_movements.length}`,
      `Raamatupidamise järjekord: ${t.accounting_export.map((x) => `${x.event_type} ${x.status}`).join(', ') || '—'}`,
    ];
    const ul = el('ul', 'trace-list'); ul.id = 'trace-list';
    for (const x of items) ul.append(el('li', null, x));
    out.replaceChildren(ul);
  }

  // ---------------------------------------------------------------- settings

  async function renderSettings(body) {
    body.replaceChildren(el('p', 'muted-p', 'Laadin…'));
    let cur;
    try { cur = (await call('/api/v1/admin/pos-settings')).data; }
    catch (e) { body.replaceChildren(stateBox('error', errText(e))); if (needsLogin()) render(root); return; }
    const hours = el('input'); hours.id = 'offline-limit'; hours.type = 'number'; hours.min = '1';
    hours.max = String(cur.offline_limit_max_hours); hours.value = String(cur.offline_limit_hours);
    const msg = el('div'); msg.id = 'settings-msg'; msg.setAttribute('role', 'status');
    const save = button('Salvesta', 'primary-btn', 'settings-save', async () => {
      const v = Number(hours.value);
      if (!Number.isInteger(v) || v < 1 || v > cur.offline_limit_max_hours) {
        msg.replaceChildren(stateBox('error', `Sisesta täisarv 1–${cur.offline_limit_max_hours}.`)); return;
      }
      try {
        const { data } = await call('/api/v1/admin/pos-settings', { method: 'PUT', body: { offline_limit_hours: v } });
        hours.value = String(data.offline_limit_hours);
        msg.replaceChildren(stateBox('ok', `Salvestatud: ${data.offline_limit_hours} h. Kassad saavad uue piirangu järgmisel ühendusel.`));
      } catch (e) { msg.replaceChildren(stateBox('error', errText(e))); }
    });
    body.replaceChildren(el('div', 'section-label', 'Võrguühenduseta müük'),
      el('p', 'muted', `Kui kaua tohib kassa müüa ilma keskserverita (1–${cur.offline_limit_max_hours} h). Piiri ületamisel kassa lukustub; tehtud müügid jäävad alles.`),
      field('Piirang (tundi)', hours), save, msg);
  }

  // -------------------------------------------------------------- quarantine

  async function renderQuarantine(body) {
    body.replaceChildren(el('p', 'muted-p', 'Laadin…'));
    let rows;
    try { rows = (await call('/api/v1/sync/quarantine')).data; }
    catch (e) { body.replaceChildren(stateBox('error', errText(e))); return; }
    body.replaceChildren(el('div', 'section-label', 'Läbivaatust ootavad kassade kirjed'),
      el('p', 'muted', 'Kassa saatis need kirjed, aga keskserver ei saanud neid rakendada. Midagi pole kadunud. Paranda põhjus (nt ava kassa) ja proovi uuesti, või lükka tagasi koos põhjusega.'));
    if (!rows.length) { body.append(stateBox('ok', 'Läbivaatust ootavaid kirjeid pole.')); return; }
    const list = el('ul', 'recon-list'); list.id = 'quarantine-list';
    for (const r of rows) {
      const li = el('li', 'recon-row warning'); li.dataset.row = String(r.id);
      const p = r.payload || {};
      const total = p.expect?.total_cents;
      li.append(el('div', 'recon-top'));
      li.firstChild.append(el('strong', null, `${ENTRY_ET[r.entry_type] || r.entry_type} · kassa ${p.register_code || '?'} · ${total != null ? formatEuro(total) : ''}`),
        el('small', null, `${r.device_id} #${r.seq} · ${new Date(r.received_at).toLocaleString('et-EE')}`));
      li.append(el('p', 'muted', r.reason || ''));
      const note = el('input'); note.placeholder = 'tagasilükkamise põhjus (vähemalt 5 märki)'; note.className = 'q-note';
      const msg = el('div'); msg.setAttribute('role', 'status');
      const retry = button('Proovi uuesti', 'primary-btn q-retry', null, async () => {
        try { await call(`/api/v1/sync/quarantine/${r.id}/retry`, { method: 'POST' }); announce('Kirje rakendati'); await renderQuarantine(body); }
        catch (e) { msg.replaceChildren(stateBox('error', errText(e))); }
      });
      const dismiss = button('Lükka tagasi', 'secondary-btn q-dismiss', null, async () => {
        if (note.value.trim().length < 5) { msg.replaceChildren(stateBox('error', 'Kirjuta põhjus (vähemalt 5 märki).')); return; }
        try { await call(`/api/v1/sync/quarantine/${r.id}/dismiss`, { method: 'POST', body: { note: note.value.trim() } }); await renderQuarantine(body); }
        catch (e) { msg.replaceChildren(stateBox('error', errText(e))); }
      });
      const actions = el('div', 'q-actions'); actions.append(retry, note, dismiss);
      li.append(actions, msg);
      list.append(li);
    }
    body.append(list);
  }

  /** The till was locked or its session ended: end the office session too
   * (server-side logout), so the next person at this till cannot use it. */
  function forget() {
    const token = st.token;
    st.token = null; st.report = null;
    if (token) api('/api/v1/auth/logout', { method: 'POST', noLock: true, headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
  }

  return { render, forget, refresh: () => (root && st.tab === 'quarantine' ? render(root) : null) };
}
