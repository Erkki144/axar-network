// AXAR ONE — office application (#76). Separate from the till: its own URL
// (/office/), its own login (password + TOTP, session kind "admin") and its
// own session token. The server refuses office tokens at the till and till
// tokens here (axar_pos/realms.py), so hiding buttons is never the protection.
//
// Session token: memory only. A reload or closing the tab ends the office
// session; nothing is shared with a till running in another tab.
import { createInventory } from './inventory.mjs';
import { createOffice } from './office.mjs';

const TIMEOUT_MS = 15000;
const LS_TENANT = 'axar.office.tenant';                       // not secret: which company to sign in to
const DEMO_IDENTITY = { 'X-Tenant-ID': 'demo', 'X-Cashier-ID': 'kontor', 'X-Role': 'manager' };

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const announce = (s) => { $('announcer').textContent = s; };
const stateBox = (cls, text) => { const d = el('div', `pay-state ${cls}`, text); d.setAttribute('role', cls === 'error' ? 'alert' : 'status'); return d; };

class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

const S = { mode: null, token: null, me: null, area: 'office', live: null, cursor: null };

function authHeaders() {
  if (S.mode === 'demo') return DEMO_IDENTITY;
  return S.token ? { Authorization: `Bearer ${S.token}` } : {};
}

async function api(path, { method = 'GET', body, headers = {} } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  let res;
  try {
    res = await fetch(path, { method, cache: 'no-store', signal: ctl.signal,
      headers: { ...authHeaders(), ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: body ? JSON.stringify(body) : undefined });
  } catch (e) {
    throw new ApiError(0, 'network', e?.name === 'AbortError' ? 'Server ei vastanud õigeaegselt.' : 'Võrguühendus puudub.');
  } finally { clearTimeout(timer); }
  const isJson = (res.headers.get('content-type') || '').includes('json');
  const data = isJson ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const msg = (data && (data.message || (typeof data.detail === 'string' ? data.detail : null))) || `Viga ${res.status}`;
    if (res.status === 401 && S.mode === 'server' && !path.startsWith('/api/v1/auth/')) { signedOut('Sessioon lõppes. Logi uuesti sisse.'); }
    throw new ApiError(res.status, data?.error, msg);
  }
  return { data, status: res.status };
}

// --------------------------------------------------------------- screens

const officeUi = createOffice({ api, el, stateBox, announce, ApiError,
  authMode: () => 'office-app',                                 // login is this app's job
  tenantId: () => S.me?.tenant_id });
const inventoryUi = createInventory({ api, el, stateBox, announce, ApiError });

function renderLogin(message) {
  $('office-nav').hidden = true; $('office-logout').hidden = true; $('office-who').hidden = true;
  const root = $('office-root');
  const form = el('form', 'office-login'); form.id = 'office-login-form';
  form.append(el('h1', null, 'Kontori sisselogimine'),
    el('p', 'muted', 'Kontor on kassast eraldi. Kassa PIN siin ei kehti: vaja on kontori kasutajat, parooli ja ühekordset koodi.'));
  const field = (label, input) => { const w = el('label', 'inv-field'); w.append(el('span', null, label), input); return w; };
  const tenant = el('input'); tenant.id = 'login-tenant'; tenant.autocomplete = 'organization';
  try { tenant.value = localStorage.getItem(LS_TENANT) || ''; } catch { /* ignore */ }
  const login = el('input'); login.id = 'login-user'; login.autocomplete = 'username';
  const pw = el('input'); pw.id = 'login-password'; pw.type = 'password'; pw.autocomplete = 'current-password';
  const totp = el('input'); totp.id = 'login-totp'; totp.inputMode = 'numeric'; totp.maxLength = 6; totp.autocomplete = 'one-time-code';
  const msg = el('div', 'dialog-error'); msg.setAttribute('role', 'alert'); if (message) msg.textContent = message;
  const go = el('button', 'primary-btn', 'Logi sisse'); go.type = 'submit'; go.id = 'login-submit';
  form.append(field('Ettevõte', tenant), field('Kasutajanimi', login), field('Parool', pw), field('Kood (TOTP)', totp), go, msg);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault(); msg.textContent = '';
    try {
      const { data } = await api('/api/v1/auth/login', { method: 'POST', body: {
        tenant_id: tenant.value.trim(), login: login.value.trim(), password: pw.value, totp: totp.value.trim() } });
      try { localStorage.setItem(LS_TENANT, tenant.value.trim()); } catch { /* ignore */ }
      S.token = data.token; pw.value = ''; totp.value = '';
      await signedIn();
    } catch (e) { msg.textContent = e.message; pw.value = ''; totp.value = ''; }
  });
  root.replaceChildren(form);
  login.focus();
}

async function signedIn() {
  const { data } = await api('/api/v1/auth/me');
  if (S.mode === 'server' && data.kind !== 'admin') { await logout('See ei ole kontori sessioon.'); return; }
  S.me = data;
  $('office-nav').hidden = false; $('office-logout').hidden = false;
  const who = $('office-who'); who.hidden = false; who.textContent = `${data.display_name || data.staff_id || ''} · ${data.tenant_name || data.tenant_id || ''}`;
  announce('Kontori sisselogimine õnnestus');
  showArea(S.area);
  startLive();
}

function showArea(area) {
  S.area = area;
  document.querySelectorAll('[data-area]').forEach((b) => b.classList.toggle('active', b.dataset.area === area));
  const root = $('office-root'); root.replaceChildren();
  if (area === 'inventory') inventoryUi.render(root); else officeUi.render(root);
}

function signedOut(message) {
  stopLive(); S.token = null; S.me = null;
  if (S.mode === 'server') renderLogin(message);
}

async function logout(message) {
  const token = S.token;
  if (token) await api('/api/v1/auth/logout', { method: 'POST' }).catch(() => {});
  signedOut(message || 'Oled välja logitud.');
}

// ----------------------------------------------------------- live updates
// The office listens only to invalidations (ids, never data) and re-reads the
// stock view through the permission-checked API.

function stopLive() { if (S.live) S.live.abort(); S.live = null; }

async function startLive() {
  stopLive();
  const ctl = new AbortController(); S.live = ctl;
  try {
    const q = S.cursor != null ? `?after=${S.cursor}` : '';
    const res = await fetch(`/api/v1/events/stream${q}`, { headers: authHeaders(), signal: ctl.signal, cache: 'no-store' });
    if (!res.ok || !res.body) throw new Error(String(res.status));
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        let type = '', id = null;
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) type = line.slice(6).trim();
          else if (line.startsWith('id:')) id = line.slice(3).trim();
          else if (type === 'hello' && line.startsWith('data:')) { try { S.cursor ??= JSON.parse(line.slice(5)).seq; } catch { /* ignore */ } }
        }
        if (id) S.cursor = Number(id);
        if (type.startsWith('stock.') && S.area === 'inventory') inventoryUi.refresh();
      }
    }
  } catch { if (ctl.signal.aborted) return; }
  if (S.live === ctl) setTimeout(() => { if (S.live === ctl) startLive(); }, 2000);
}

// ------------------------------------------------------------------ start

document.querySelectorAll('[data-area]').forEach((b) => b.addEventListener('click', () => showArea(b.dataset.area)));
$('office-logout').addEventListener('click', () => logout());

(async function start() {
  try { S.mode = (await api('/api/v1/auth/config')).data.mode; }
  catch (e) { $('office-root').replaceChildren(stateBox('error', `Serveriga ei saanud ühendust: ${e.message}`)); return; }
  if (S.mode === 'demo') { await signedIn(); return; }
  renderLogin();
})();
