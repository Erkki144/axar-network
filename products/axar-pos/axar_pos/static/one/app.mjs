// AXAR ONE cashier UI connected to the AXAR POS API (issue #11).
//
// Rules this file follows (see products/axar-pos/docs/SLICE_3_UI_INTEGRATION.md):
// - The server owns prices, VAT, rounding, cash due, change and receipts. The
//   browser only formats numbers it received and parses what the cashier typed.
// - A sale or refund is shown as done ONLY after the server confirmed it.
// - Every payment attempt has one Idempotency-Key. A retry after a network
//   failure re-sends the SAME key and body, so it can never double-charge.
// - Table bills and kitchen tickets live on the SERVER (slice 4): every device
//   of the tenant sees the same bill. Each edit is a compare-and-swap on the
//   order version, so a stale device gets 409 and reloads instead of
//   overwriting; paying a bill closes it in the same server transaction.
import { centsToInput, formatEuro, parseEuroInput } from './format.mjs';

// Identity. In "server" mode (RFC-0002) every request carries the device
// token (this enrolled till) and the staff session token from the PIN login.
// In "demo" mode the server trusts these headers WITHOUT authentication.
const IDENTITY = { 'X-Tenant-ID': 'demo', 'X-Cashier-ID': 'kassa1' };
const DEMO_MANAGER = { 'X-Role': 'manager' };
const LS_DEVICE = 'axar.one.device.v1';   // device token: survives reloads and logouts
const SS_SESSION = 'axar.one.session.v1'; // staff session: this tab only, ends with the tab
const AUTH = { mode: 'demo', device: null, session: null, me: null, deviceInfo: null };
const readStore = (store, key) => { try { return store.getItem(key); } catch { return null; } };
const writeStore = (store, key, v) => { try { if (v == null) store.removeItem(key); else store.setItem(key, v); } catch { /* ignore */ } };
function authHeaders() {
  if (AUTH.mode === 'demo') return IDENTITY;
  const h = {};
  if (AUTH.device) h['X-Device-Token'] = AUTH.device;
  if (AUTH.session) h.Authorization = `Bearer ${AUTH.session}`;
  return h;
}
const TIMEOUT_MS = 8000;
const LS_REGISTER = 'axar.one.register';
// Crash-safe journal of the ONE in-flight money operation (sale or refund).
// Written BEFORE the POST, cleared only after a definitive server answer.
const LS_PENDING = 'axar.one.pending.v1';
const PENDING_PATHS = [/^\/api\/v1\/sales\/cash$/, /^\/api\/v1\/sales\/[0-9a-f-]{36}\/refunds$/,
  /^\/api\/v1\/orders\/[0-9a-f-]{36}\/pay-cash$/, /^\/api\/v1\/orders\/[0-9a-f-]{36}\/payments$/,
  /^\/api\/v1\/drawer\/\d+\/movements$/];
const ICONS = { Kohv: ['☕', 'sand'], Toit: ['🥐', 'rose'], Joogid: ['💧', 'sky'], Muud: ['📦', 'lilac'] };

// ------------------------------------------------------------------ HTTP layer

class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}
class NetworkError extends Error {}

async function api(path, { method = 'GET', body, headers = {}, noLock = false } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { ...authHeaders(), ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: body ? JSON.stringify(body) : undefined,
      signal: ctl.signal,
      cache: 'no-store',
    });
  } catch (e) {
    setOnline(false);
    throw new NetworkError(e?.name === 'AbortError' ? 'Server ei vastanud õigeaegselt.' : 'Võrguühendus puudub.');
  } finally {
    clearTimeout(timer);
  }
  setOnline(true);
  const isJson = (res.headers.get('content-type') || '').includes('json');
  const data = isJson ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    let msg = (data && (data.message || (typeof data.detail === 'string' ? data.detail : null))) || `Viga ${res.status}`;
    if (data && OFFLINE_MSG[data.error]) { msg = OFFLINE_MSG[data.error]; refreshOfflineBanner(); }
    // Session expired / locked / revoked: lock the till. Pending money state
    // (journal) stays and is resumed with the SAME key after the next login.
    if (res.status === 401 && AUTH.mode === 'server' && !noLock && !path.startsWith('/api/v1/auth/')) sessionLost();
    throw new ApiError(res.status, data?.error, msg);
  }
  return { data, status: res.status };
}

/** Failures where the server may or may not have recorded the request. */
/**
 * Only answers that PROVE the operation was not recorded are definitive. The
 * server looks up the Idempotency-Key before any business check, so these
 * business rejections on a retry mean "no record with this key". Everything
 * else (network, 5xx, 401/403/429, 409 key-reused/conflict, unknown) is
 * uncertain: the journal stays and only a same-key retry is allowed.
 */
// order_changed: the server looks the key up before the order CAS, so a
// same-key retry of a RECORDED payment replays the sale instead.
// approval_invalid: the server checks the key (replay) BEFORE the approval, so
// it proves "no record with this key". A plain 403 (role) does NOT: the role
// is checked first and the original may have been sent by another session.
// offline_limit_exceeded / clock_moved_back (423): the till's offline guard runs
// AFTER the key lookup, so a same-key retry of a recorded sale would replay it;
// a 423 therefore proves "nothing recorded with this key".
const DEFINITIVE_CODES = new Set(['no_open_drawer', 'insufficient_tender', 'insufficient_drawer_cash',
  'over_refund', 'invalid_refund', 'not_found', 'order_changed', 'approval_invalid',
  'offline_limit_exceeded', 'clock_moved_back']);
const isDefinitive = (e) => e instanceof ApiError && (e.status === 422 || (DEFINITIVE_CODES.has(e.code) && [403, 404, 409, 422, 423].includes(e.status)));
const isUncertain = (e) => !isDefinitive(e);

// ----------------------------------------------------------------------- state

const S = {
  products: [], registers: [], registerId: null, drawer: null,
  mode: 'express', category: 'Kõik',
  tables: [], tablesError: '', tableId: null, order: null, // server-side table bills
  orderBusy: false, orderQueue: Promise.resolve(), orderGen: 0, orderMsg: { text: '', cls: '' },
  bill: null, // table bill payment dialog (C3)
  tickets: [], ticketsError: '', kitchenPending: null, pollTimer: null, pollBusy: false, lastPoll: 0,
  walkin: {},                 // registerId -> {productId: qty}, memory only
  quote: null, quoteSig: '', quoteError: '', quoteSeq: 0, quoteTimer: null,
  payment: null,              // pending/last payment attempt
  refund: null,               // pending refund attempt
  cashMove: null,             // pending physical cash movement (#68)
  recovery: null,             // blocking recovery of a persisted pending op
  storageOk: true,            // false => money operations are blocked
  locksOk: true,              // false => no cross-tab lock => money operations blocked
  online: true,
};
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const announce = (s) => { $('announcer').textContent = s; };
const product = (id) => S.products.find((p) => p.id === id);
const register = () => S.registers.find((r) => r.id === S.registerId);
const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(16).slice(2)}`);

function cartKey() { return S.mode === 'tables' ? `order:${S.order?.id || 'none'}` : `walkin:${S.registerId}`; }
/** Tables: a read-only view of the SERVER order; edits go through setOrderLines(). */
function currentCart() {
  if (S.mode === 'tables') {
    const out = {};
    for (const ln of S.order?.lines || []) out[ln.product_id] = ln.quantity;
    return out;
  }
  return (S.walkin[S.registerId] ||= {});
}
const sentQuantity = (pid) => (S.mode === 'tables' ? (S.order?.lines.find((l) => l.product_id === pid)?.sent_quantity || 0) : 0);
function cartLines(cart = currentCart()) {
  return Object.entries(cart)
    .map(([pid, qty]) => ({ product: product(Number(pid)), quantity: qty }))
    .filter((x) => x.product && x.quantity > 0);
}
const requestLines = (lines) => lines.map((x) => ({ product_id: x.product.id, quantity: x.quantity }));
const cartSig = (lines) => JSON.stringify([S.registerId, S.mode === 'tables' ? [S.order?.id, S.order?.version] : null, requestLines(lines)]);
const paymentLocked = () => Boolean(
  (S.payment && ['sending', 'unconfirmed'].includes(S.payment.state))
  || (S.refund && ['sending', 'unconfirmed'].includes(S.refund.state))
  || S.recovery,
);

// ------------------------------------------------------- pending-op journal

function storageWorks() {
  try {
    localStorage.setItem('axar.one.probe', '1');
    localStorage.removeItem('axar.one.probe');
    return true;
  } catch { return false; }
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const isInt = (v, min, max) => Number.isSafeInteger(v) && v >= min && v <= max;
const onlyKeys = (o, keys) => o && typeof o === 'object' && !Array.isArray(o) && Object.keys(o).every((k) => keys.includes(k));

/** Full schema + op/path consistency check of a journal record. */
function validRecord(rec) {
  if (!onlyKeys(rec, ['v', 'op', 'key', 'path', 'body', 'headers', 'cartKey', 'saleId', 'registerId', 'createdAt'])) return false;
  if (rec.v !== 1 || typeof rec.key !== 'string' || rec.key.length < 8 || rec.key.length > 128) return false;
  if (typeof rec.createdAt !== 'string' || Number.isNaN(Date.parse(rec.createdAt))) return false;
  const b = rec.body;
  if (!b || !isInt(rec.registerId, 1, Number.MAX_SAFE_INTEGER)) return false;
  if (rec.op !== 'cashmove' && (!isInt(b.register_id, 1, Number.MAX_SAFE_INTEGER) || rec.registerId !== b.register_id)) return false;
  const okLines = () => Array.isArray(b.lines) && b.lines.length > 0 && b.lines.length <= 200;
  if (rec.op === 'sale') {
    if (!isInt(b.tendered_cents, 0, 100_000_000) || !onlyKeys(rec.headers || {}, [])) return false;
    if (rec.path === '/api/v1/sales/cash') {
      // 'table:r:i' = device-local demo bill of PR #12; accepted so an old
      // pending record can still be resolved after an upgrade.
      return onlyKeys(b, ['register_id', 'lines', 'tendered_cents']) && okLines()
        && b.lines.every((l) => onlyKeys(l, ['product_id', 'quantity']) && isInt(l.product_id, 1, Number.MAX_SAFE_INTEGER) && isInt(l.quantity, 1, 999))
        && typeof rec.cartKey === 'string' && /^(walkin:\d+|table:\d+:\d+)$/.test(rec.cartKey);
    }
    const m = /^\/api\/v1\/orders\/([0-9a-f-]{36})\/pay-cash$/.exec(rec.path);
    return Boolean(m) && UUID_RE.test(m[1]) && onlyKeys(b, ['version', 'register_id', 'tendered_cents'])
      && isInt(b.version, 1, 1_000_000_000) && rec.cartKey === `order:${m[1]}`;
  }
  if (rec.op === 'billpay') {
    // One tender against a table bill (C3). Same strict shape as the server.
    const m = /^\/api\/v1\/orders\/([0-9a-f-]{36})\/payments$/.exec(rec.path);
    return Boolean(m) && UUID_RE.test(m[1]) && rec.cartKey === `order:${m[1]}` && onlyKeys(rec.headers || {}, [])
      && onlyKeys(b, ['method', 'register_id', 'expected_total_cents', 'amount_cents', 'tendered_cents', 'simulate', 'customer_name'])
      && ['cash', 'card', 'transfer'].includes(b.method) && isInt(b.expected_total_cents, 0, 100_000_000)
      && isInt(b.amount_cents, 1, 100_000_000) && (b.tendered_cents === undefined || isInt(b.tendered_cents, 0, 100_000_000))
      && (b.simulate === undefined || ['approved', 'declined', 'timeout'].includes(b.simulate))
      && (b.customer_name === undefined || (typeof b.customer_name === 'string' && b.customer_name.length <= 200));
  }
  if (rec.op === 'cashmove') {
    const m = /^\/api\/v1\/drawer\/(\d+)\/movements$/.exec(rec.path);
    return Boolean(m) && isInt(Number(m[1]), 1, Number.MAX_SAFE_INTEGER)
      && onlyKeys(b, ['direction', 'amount_cents', 'reason_code', 'reason_text'])
      && ['in', 'out'].includes(b.direction)
      && isInt(b.amount_cents, 1, 100_000_000)
      && ['SAFE_DROP','FLOAT_TOPUP','CASH_CORRECTION','BANK_DEPOSIT','OTHER'].includes(b.reason_code)
      && typeof b.reason_text === 'string' && b.reason_text.trim().length >= 5 && b.reason_text.length <= 300
      && onlyKeys(rec.headers || {}, ['X-Approval-Id'])
      && (rec.headers?.['X-Approval-Id'] === undefined || UUID_RE.test(rec.headers['X-Approval-Id']));
  }
  if (rec.op === 'refund') {
    if (!okLines()) return false;
    return typeof rec.saleId === 'string' && UUID_RE.test(rec.saleId)
      && rec.path === `/api/v1/sales/${rec.saleId}/refunds` && onlyKeys(b, ['register_id', 'lines', 'reason'])
      && typeof b.reason === 'string' && b.reason.length >= 3 && b.reason.length <= 500
      && b.lines.every((l) => onlyKeys(l, ['line_no', 'quantity']) && isInt(l.line_no, 1, 10_000) && isInt(l.quantity, 1, 999))
      && onlyKeys(rec.headers || {}, ['X-Role', 'X-Approval-Id'])
      && (rec.headers?.['X-Role'] === undefined || rec.headers['X-Role'] === 'manager')
      && (rec.headers?.['X-Approval-Id'] === undefined || UUID_RE.test(rec.headers['X-Approval-Id']));
  }
  return false;
}

/** {state:'none'} | {state:'ok', rec} | {state:'corrupt'} | {state:'unreadable'} */
function readPending() {
  let raw;
  // A failing read is NOT "no record": fail closed.
  try { raw = localStorage.getItem(LS_PENDING); } catch { return { state: 'unreadable' }; }
  if (raw == null) return { state: 'none' };
  try {
    const rec = JSON.parse(raw);
    return validRecord(rec) && PENDING_PATHS.some((re) => re.test(rec.path)) ? { state: 'ok', rec } : { state: 'corrupt' };
  } catch { return { state: 'corrupt' }; }
}

/** Throws if the record cannot be stored: the caller must then NOT send. */
function writePending(rec) {
  const raw = JSON.stringify({ v: 1, ...rec });
  localStorage.setItem(LS_PENDING, raw);
  if (localStorage.getItem(LS_PENDING) !== raw) throw new Error('verify failed');
}

function clearPending(key) {
  try {
    const cur = readPending();
    // Only remove our own record (another tab may have written a newer one).
    if (key == null || (cur.state === 'ok' && cur.rec.key === key) || cur.state === 'corrupt') localStorage.removeItem(LS_PENDING);
  } catch { /* nothing stored */ }
}

function setOnline(on) {
  S.online = on;
  const pill = $('net-status');
  // Keep the realtime marker: any successful API call also lands here, and must
  // not hide that the live stream is connected (state lives on the pill itself).
  const live = on && pill.dataset.live === '1';
  pill.textContent = on ? (live ? '● Server ühendatud · reaalajas' : '● Server ühendatud') : '● Ühendus puudub';
  pill.classList.toggle('offline', !on);
  pill.classList.toggle('online', on);
}

// --------------------------------------------------------------- cart + quote

function addProduct(id) {
  if (paymentLocked()) { announce('Pooleli makse: lõpeta see enne ostukorvi muutmist.'); return; }
  const p = product(id);
  if (!p) return;
  if (S.mode === 'tables') {
    mutateOrder((c) => { const n = (c[id] || 0) + 1; if (n > 999) return false; c[id] = n; return true; }, `${p.name} lisatud`);
    return;
  }
  const c = currentCart();
  const n = (c[id] || 0) + 1;
  if (n > 999) return;
  c[id] = n;
  cartChanged();
  announce(`${p.name} lisatud`);
}

function changeQuantity(id, delta) {
  if (paymentLocked()) { announce('Pooleli makse: lõpeta see enne ostukorvi muutmist.'); return; }
  const apply = (c) => {
    const n = (c[id] || 0) + delta;
    if (n < sentQuantity(id)) { setOrderMessage('Kööki saadetud kogust ei saa vähendada.', 'error'); return false; }
    if (n > 999) return false;
    if (n <= 0) delete c[id]; else c[id] = n;
    return true;
  };
  if (S.mode === 'tables') { mutateOrder(apply); return; }
  if (apply(currentCart())) cartChanged();
}

function cartChanged() { renderBasket(); scheduleQuote(); }

function scheduleQuote() {
  clearTimeout(S.quoteTimer);
  const lines = cartLines();
  const sig = cartSig(lines);
  if (!lines.length) { S.quote = null; S.quoteSig = sig; S.quoteError = ''; renderTotals(); return; }
  if (S.quote && S.quoteSig === sig) { renderTotals(); return; }
  S.quote = null; S.quoteError = '';
  renderTotals();
  const seq = ++S.quoteSeq;
  S.quoteTimer = setTimeout(async () => {
    try {
      const { data } = await api('/api/v1/sales/quote', { method: 'POST', body: { lines: requestLines(lines), register_id: S.registerId } });
      if (seq !== S.quoteSeq) return;
      S.quote = data; S.quoteSig = sig;
    } catch (e) {
      if (seq !== S.quoteSeq) return;
      S.quoteError = e.message;
    }
    renderBasket(); // line amounts + totals from the server quote
  }, 120);
}

// ------------------------------------------------------------------- rendering

function renderBasket() {
  const panel = $('checkout-panel');
  panel.hidden = S.mode === 'kitchen' || S.mode === 'manager';
  document.querySelector('.app-shell').classList.toggle('no-checkout', panel.hidden);
  if (panel.hidden) return;
  const tableMode = S.mode === 'tables';
  const lines = cartLines();
  $('basket-title').textContent = !tableMode ? 'Kassamüük'
    : S.order ? `${S.order.table_name} · arve` : S.tableId ? 'Avan arvet…' : 'Vali laud';
  $('basket-count').textContent = String(lines.reduce((n, x) => n + x.quantity, 0));
  const box = $('basket-items');
  box.replaceChildren();
  if (!lines.length) {
    const empty = el('div', 'empty-basket');
    const noTable = tableMode && !S.order;
    empty.append(el('span', null, '◇'), el('strong', null, noTable ? 'Vali laud' : 'Alusta tellimust'),
      el('small', null, noTable ? 'Puuduta lauda, et selle arve avada.' : 'Puuduta toodet, et see ostukorvi lisada.'));
    empty.firstChild.setAttribute('aria-hidden', 'true');
    box.append(empty);
  }
  for (const { product: p, quantity } of lines) {
    const row = el('div', 'cart-row');
    row.dataset.product = String(p.id);
    const [icon] = ICONS[p.category] || ICONS.Muud;
    const body = el('div', 'cart-body');
    const ctr = el('div', 'cart-controls');
    const minus = el('button', 'qty-btn', '−'); minus.type = 'button'; minus.setAttribute('aria-label', `Vähenda: ${p.name}`);
    minus.disabled = tableMode && quantity <= sentQuantity(p.id);
    minus.addEventListener('click', () => changeQuantity(p.id, -1));
    const plus = el('button', 'qty-btn', '+'); plus.type = 'button'; plus.setAttribute('aria-label', `Lisa: ${p.name}`);
    plus.addEventListener('click', () => changeQuantity(p.id, 1));
    ctr.append(minus, el('span', 'qty-count', String(quantity)), plus);
    const sent = sentQuantity(p.id);
    body.append(el('div', 'cart-name', p.name),
      el('div', 'cart-detail', `${formatEuro(p.price_cents)} / tk${tableMode && sent ? ` · köögis ${sent}` : ''}`), ctr);
    // Line gross from the server quote when available (price × qty is display only).
    const ql = S.quote && S.quoteSig === cartSig(lines) ? S.quote.lines.find((l) => l.product_id === p.id && l.quantity === quantity) : null;
    row.append(el('span', 'cart-icon', icon), body, el('strong', 'cart-price', ql ? formatEuro(ql.gross_cents) : '…'));
    box.append(row);
  }
  $('send-button').hidden = !tableMode;
  if (tableMode) {
    const unsent = lines.some((x) => x.quantity > sentQuantity(x.product.id));
    const retry = S.kitchenPending && S.kitchenPending.orderId === S.order?.id;
    $('send-button').disabled = !S.order || S.orderBusy || !unsent;
    $('send-button').textContent = !lines.length ? 'Saada kööki ↗'
      : unsent ? (retry ? 'Proovi uuesti: saada kööki ↗' : 'Saada kööki ↗') : 'Kõik tooted kööki saadetud ✓';
  }
  renderTotals();
}

/** Phone: the basket sits below the menu, so a sticky bar keeps count, total
 * and the pay action in view while adding products. */
function renderMiniCart(lines, q) {
  const bar = $('mini-cart');
  if (!bar) return;
  const n = lines.reduce((s, x) => s + x.quantity, 0);
  bar.hidden = !n || !['express', 'tables'].includes(S.mode);
  $('mini-count').textContent = `${n} ${n === 1 ? 'toode' : 'toodet'}`;
  $('mini-total').textContent = q ? formatEuro(q.cash_due_cents) : '…';
}

function renderTotals() {
  const lines = cartLines();
  const ready = S.quote && S.quoteSig === cartSig(lines);
  const q = ready ? S.quote : null;
  $('subtotal').textContent = q ? formatEuro(q.total_cents) : lines.length ? '…' : formatEuro(0);
  $('vat').textContent = q ? formatEuro(q.vat_cents) : lines.length ? '…' : formatEuro(0);
  const r = q ? q.cash_rounding_cents : 0;
  $('rounding').textContent = `${r > 0 ? '+' : ''}${formatEuro(r)}`;
  $('total').textContent = q ? formatEuro(q.cash_due_cents) : lines.length ? '…' : formatEuro(0);
  const state = $('quote-state');
  state.classList.toggle('bad', Boolean(S.quoteError) || !S.drawer || !S.storageOk || !S.locksOk || Boolean(S.recovery));
  if (!S.storageOk) state.textContent = 'Seadme salvestus ei tööta – makseid ei saa turvaliselt teha (topelttehingu oht).';
  else if (!S.locksOk) state.textContent = 'Brauser ei toeta akende lukustust – makseid ei saa turvaliselt teha.';
  else if (S.recovery) state.textContent = 'Pooleli tehing ootab lõpetamist.';
  else if (!S.drawer) state.textContent = 'Kassa on suletud – ava kassa, et müüa.';
  else if (S.quoteError) state.textContent = `Hinda ei saanud serverist: ${S.quoteError}`;
  else if (lines.length && !ready) state.textContent = 'Arvutan serveris…';
  else if (ready) state.textContent = 'Summad arvutas server.';
  else state.textContent = '';
  renderMiniCart(lines, q);
  const pay = $('pay-button');
  const resuming = (S.payment?.state === 'unconfirmed' && S.payment.sig === cartSig(lines))
    || (S.mode === 'tables' && S.bill?.state === 'unconfirmed' && S.bill.orderId === S.order?.id);
  const orderReady = S.mode !== 'tables' || (S.order && !S.orderBusy);
  pay.disabled = !S.storageOk || !S.locksOk || Boolean(S.recovery) || (!(S.drawer && ready && lines.length && orderReady) && !resuming);
  const partly = S.mode === 'tables' && S.order?.status === 'partially_paid';
  pay.firstChild.textContent = resuming ? 'Lõpeta pooleli makse ' : partly ? 'Maksa ülejäänu ' : 'Võta sularaha ';
  const split = $('split-button');
  split.hidden = S.mode !== 'tables';
  split.disabled = pay.disabled && !resuming;
}

function renderDrawerStatus() {
  const box = $('drawer-status');
  box.replaceChildren();
  const reg = register();
  $('register-name').textContent = reg ? reg.name : 'Kassa puudub';
  $('location-name').textContent = reg ? reg.location.name : 'AXAR ONE';
  if (!reg) return;
  if (!S.drawer) {
    box.append(el('span', null, 'Kassa on suletud.'));
    const b = el('button', 'secondary-btn', 'Ava kassa'); b.type = 'button'; b.id = 'open-drawer';
    b.addEventListener('click', openDrawerDialog);
    box.append(b);
  } else {
    const d = S.drawer;
    box.append(el('span', null, 'Vahetus #' + d.id + ' avatud · ' + d.sale_count + ' müüki · oodatav sularaha ' + formatEuro(d.expected_cents)));
    const cashIn = el('button', 'secondary-btn', 'Sularaha sisse'); cashIn.type = 'button'; cashIn.id = 'cash-in';
    cashIn.addEventListener('click', () => openCashMovementDialog('in'));
    const cashOut = el('button', 'secondary-btn', 'Sularaha välja'); cashOut.type = 'button'; cashOut.id = 'cash-out';
    cashOut.addEventListener('click', () => openCashMovementDialog('out'));
    box.append(cashIn, cashOut);
  }
}

function renderRegisterSelect() {
  const sel = $('register-select');
  sel.replaceChildren();
  for (const r of S.registers) {
    const o = el('option', null, `${r.location.name} · ${r.name}`);
    o.value = String(r.id);
    sel.append(o);
  }
  sel.value = String(S.registerId);
  // The provisioned device fixes the register. There is no register picker
  // in the authenticated cashier workspace; demo keeps a testing switch.
  const isTill = AUTH.mode === 'server';
  sel.disabled = isTill;
  sel.hidden = isTill;
  sel.style.display = isTill ? 'none' : '';
  const fixed = $('register-fixed-label');
  fixed.hidden = !isTill;
  fixed.textContent = (S.registers.find((r) => r.id === S.registerId)?.location.name || 'Asukoht') +
    ' · ' + (S.registers.find((r) => r.id === S.registerId)?.name || 'Kassa määramata');
}

function activateMode(next) {
  if (!['express', 'tables', 'kitchen', 'manager'].includes(next)) return;
  S.mode = next; S.category = 'Kõik';
  document.querySelectorAll('.nav-item').forEach((b) => {
    const on = b.dataset.mode === next;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  const copy = {
    express: ['MÜÜGI TÖÖLAUD', ['Kiire teenindus.', 'Vähem samme.'], 'Tooted ja hinnad tulevad AXAR POS serverist.', 'Hinnad, käibemaks ja tagastusraha tulevad serverist.'],
    tables: ['RESTORANI TÖÖLAUD', ['Laudade teenindus.', 'Üks selge voog.'], 'Lauaarved on serveris – kõik seadmed näevad sama arvet.', 'Arve tasumine teeb müügi ja sulgeb arve ühe serveritehinguna.'],
    kitchen: ['KÖÖGI TÖÖLAUD', ['Kõik tellimused.', 'Õiges järjekorras.'], 'Köögipiletid tulevad serverist ja uuenevad iseenesest.', 'Uued piletid on vanimast uuemani.'],
    manager: ['JUHI TÖÖLAUD', ['Päeva ülevaade.', 'Päris andmetest.'], 'Aruanne, müügid ja tagastused tulevad serverist.', 'Tagastus kasutab DEMO juhi rolli, mitte päris autentimist.'],
  }[next];
  $('section-eyebrow').textContent = copy[0];
  const h = $('section-title'); h.replaceChildren(document.createTextNode(copy[1][0]), el('br'), el('em', null, copy[1][1]));
  $('section-subtitle').textContent = copy[2];
  $('hint-message').textContent = copy[3];
  startPolling();
  if (next === 'kitchen') renderKitchen();
  else if (next === 'manager') renderManager();
  else if (next === 'tables') renderTables();
  else renderExpress();
  renderBasket();
  scheduleQuote();
}

function renderProductGrid(root) {
  const wrapper = el('section'); wrapper.id = 'product-module';
  const toolbar = el('div', 'toolbar');
  const head = el('div'); head.append(el('div', 'section-label', 'Menüü'), el('div', 'section-caption', 'Vali toode ühe puudutusega'));
  const search = el('label', 'search-wrap');
  const input = el('input'); input.id = 'product-search'; input.type = 'search'; input.placeholder = 'Otsi toodet... (/)';
  input.setAttribute('aria-label', 'Otsi toodet'); input.autocomplete = 'off';
  search.append(el('span', null, '⌕'), input);
  toolbar.append(head, search);
  const cats = el('div', 'category-bar'); cats.id = 'category-bar';
  const grid = el('div', 'product-grid'); grid.id = 'product-grid';
  wrapper.append(toolbar, cats, grid);
  root.append(wrapper);
  const categories = ['Kõik', ...new Set(S.products.map((p) => p.category))];
  for (const c of categories) {
    const b = el('button', `category-btn${c === S.category ? ' active' : ''}`, c); b.type = 'button';
    b.addEventListener('click', () => { S.category = c; cats.querySelectorAll('button').forEach((x) => x.classList.toggle('active', x.textContent === c)); filterProducts(); });
    cats.append(b);
  }
  for (const p of S.products) {
    const [icon, tone] = ICONS[p.category] || ICONS.Muud;
    const b = el('button', 'product'); b.type = 'button';
    b.dataset.product = String(p.id); b.dataset.category = p.category; b.dataset.name = p.name.toLocaleLowerCase('et');
    b.setAttribute('aria-label', `Lisa ostukorvi: ${p.name}, ${formatEuro(p.price_cents)}`);
    const media = el('div', `product-media ${tone}`, icon); media.setAttribute('aria-hidden', 'true');
    b.append(media, el('span', 'product-name', p.name), el('span', 'product-price', formatEuro(p.price_cents)));
    b.addEventListener('click', () => addProduct(p.id));
    grid.append(b);
  }
  input.addEventListener('input', filterProducts);
  filterProducts();
}

function filterProducts() {
  const input = $('product-search');
  if (!input) return;
  const q = input.value.toLocaleLowerCase('et').trim();
  let visible = 0;
  document.querySelectorAll('#product-grid .product').forEach((b) => {
    const ok = (S.category === 'Kõik' || S.category === b.dataset.category) && b.dataset.name.includes(q);
    b.hidden = !ok; if (ok) visible++;
  });
  let empty = $('no-products');
  if (!visible && !empty) { empty = el('p', 'muted-p', 'Tooteid ei leitud. Proovi teist otsingut.'); empty.id = 'no-products'; $('product-module').append(empty); }
  if (empty) empty.hidden = visible !== 0;
}

function renderExpress() { const root = $('view-root'); root.replaceChildren(); renderProductGrid(root); }

const identityNote = () => (AUTH.mode === 'server' ? '' : 'Ettevõte ja kassapidaja on DEMO päised, mitte sisselogimine (RFC-0002).');

function renderTables() {
  const root = $('view-root'); root.replaceChildren();
  const section = el('section');
  if (AUTH.mode === 'demo') section.append(el('div', 'demo-banner', identityNote()));
  const msg = el('div', 'order-message'); msg.id = 'order-message'; msg.setAttribute('role', 'status');
  const grid = el('div', 'table-grid'); grid.id = 'table-grid';
  section.append(msg, grid);
  root.append(section);
  renderTableGrid();
  renderProductGrid(root);
  refreshTables();
}

function renderTableGrid() {
  const grid = $('table-grid');
  if (!grid) return;
  grid.replaceChildren();
  if (S.tablesError) grid.append(stateBox('error', `Laudu ei saanud serverist laadida: ${S.tablesError}`));
  for (const tb of S.tables) {
    const count = tb.open_order?.item_count || 0;
    const b = el('button', `table-card${tb.id === S.tableId ? ' selected' : ''}${count ? ' occupied' : ''}`); b.type = 'button';
    b.dataset.table = tb.code;
    const partly = tb.open_order?.status === 'partially_paid';
    b.append(el('strong', null, tb.name), el('small', null, count ? `${partly ? 'Osaliselt tasutud' : 'Arve avatud'} · ${count} tk` : 'Vaba'));
    b.addEventListener('click', () => selectTable(tb.id));
    grid.append(b);
  }
  const msg = $('order-message');
  if (msg) { msg.textContent = S.orderMsg.text; msg.className = `order-message ${S.orderMsg.cls}`; msg.hidden = !S.orderMsg.text; }
}

function setOrderMessage(text, cls = '') {
  S.orderMsg = { text, cls };
  if (text) announce(text);
  renderTableGrid();
}

/** Keep the table card's item count in step with our own edits until the next poll. */
function syncTableSummary() {
  if (!S.order) return;
  const tb = S.tables.find((x) => x.id === S.order.table_id);
  if (tb) tb.open_order = { id: S.order.id, version: S.order.version, status: S.order.status, item_count: S.order.lines.reduce((n, l) => n + l.quantity, 0) };
}

function refreshTableView() {
  syncTableSummary();
  if (S.mode === 'tables') renderTableGrid();
  renderBasket(); scheduleQuote();
}

async function loadTables() {
  try { S.tables = (await api('/api/v1/tables')).data; S.tablesError = ''; } catch (e) { S.tablesError = e.message; }
}

/** Re-read the open order from the server; never replaces it with an OLDER version. */
async function reloadOrder() {
  const cur = S.order;
  if (!cur) return;
  try {
    const { data } = await api(`/api/v1/orders/${cur.id}`);
    if (S.order?.id !== data.id) return;
    if (!['open', 'partially_paid'].includes(data.status)) {
      S.order = null; S.tableId = null; S.orderGen++;
      setOrderMessage(`${data.table_name}: arve on tasutud (teises seadmes või aknas).`);
      return;
    }
    if (data.version >= S.order.version) S.order = data;
  } catch (e) {
    if (!(e instanceof NetworkError) && S.order?.id === cur.id) { S.order = null; S.tableId = null; S.orderGen++; }
  }
}

/** Poll: table list + our bill. Skipped while a money operation is pending. */
async function refreshTables() {
  await loadTables();
  let changed = false;
  if (S.order && !S.orderBusy && !paymentLocked()) {
    const summary = S.tables.find((x) => x.id === S.order.table_id)?.open_order;
    if (!summary || summary.id !== S.order.id || summary.version !== S.order.version) {
      const before = S.order?.version;
      await reloadOrder();
      changed = S.order?.version !== before || !S.order;
    }
  }
  if (S.mode !== 'tables') return;
  syncTableSummary();
  renderTableGrid();
  if (changed) { renderBasket(); scheduleQuote(); }
}

/** Opening the bill goes through the same queue as edits, so taps made right
 * after choosing a table are applied to that table's bill, not dropped. */
function selectTable(id) {
  if (paymentLocked()) { announce('Pooleli makse: lõpeta see enne laua vahetamist.'); return; }
  const gen = ++S.orderGen; // drop edits queued for the previous bill
  S.tableId = id; S.order = null; S.kitchenPending = null; S.orderMsg = { text: '', cls: '' };
  refreshTableView();
  S.orderQueue = S.orderQueue.then(async () => {
    if (gen !== S.orderGen) return;
    try {
      const { data } = await api(`/api/v1/tables/${id}/order`, { method: 'POST' }); // get-or-create
      if (gen !== S.orderGen) return;
      S.order = data;
      announce(`Valitud ${data.table_name}`);
    } catch (e) {
      if (gen !== S.orderGen) return;
      S.tableId = null;
      setOrderMessage(`Arvet ei saanud avada: ${e.message}`, 'error');
    }
    refreshTableView();
  }).catch(() => {});
}

/**
 * Edits of the server bill are serialized: each queued edit is applied to the
 * LATEST order and sent with its version. A 409 means another device changed
 * the bill first: we reload, show it, and drop the edits still queued (they
 * were meant for the bill as it looked before).
 */
function mutateOrder(apply, okMsg = '') {
  const gen = S.orderGen;
  S.orderQueue = S.orderQueue.then(async () => {
    if (gen !== S.orderGen || !S.order || paymentLocked()) return;
    const cart = currentCart();
    if (!apply(cart)) { refreshTableView(); return; }
    const lines = Object.entries(cart).filter(([, q]) => q > 0).map(([pid, q]) => ({ product_id: Number(pid), quantity: q }));
    const order = S.order;
    S.orderBusy = true; renderBasket();
    try {
      const { data } = await api(`/api/v1/orders/${order.id}/lines`, { method: 'PUT', body: { version: order.version, lines } });
      if (S.order?.id === data.id) S.order = data;
      S.orderMsg = { text: '', cls: '' };
      if (okMsg) announce(okMsg);
    } catch (e) {
      S.orderGen++;
      S.orderBusy = false;
      if (e instanceof ApiError && e.code === 'order_changed') {
        await reloadOrder();
        setOrderMessage('Arvet muudeti teises seadmes – näitan värsket arvet. Korda viimast muudatust.', 'error');
      } else if (e instanceof ApiError && e.code === 'order_not_open') {
        await reloadOrder();
        if (S.order?.status === 'partially_paid') setOrderMessage('Arve on osaliselt tasutud – ridu enam ei muudeta. Jätka maksmist.', 'error');
      } else {
        setOrderMessage(`Arvet ei salvestatud: ${e.message}`, 'error');
      }
    } finally { S.orderBusy = false; }
    refreshTableView();
  }).catch(() => { S.orderBusy = false; });
  return S.orderQueue;
}

function renderKitchen() {
  const root = $('view-root'); root.replaceChildren();
  if (AUTH.mode === 'demo') root.append(el('div', 'demo-banner', identityNote()));
  const grid = el('div', 'kds-grid'); grid.id = 'kds-grid';
  root.append(grid);
  renderTicketGrid();
  loadTickets();
}

async function loadTickets() {
  try { S.tickets = (await api('/api/v1/kitchen/tickets')).data; S.ticketsError = ''; } catch (e) { S.ticketsError = e.message; }
  if (S.mode === 'kitchen') renderTicketGrid();
}

function renderTicketGrid() {
  const grid = $('kds-grid');
  if (!grid) return;
  grid.replaceChildren();
  if (S.ticketsError) grid.append(stateBox('error', `Pileteid ei saanud serverist laadida: ${S.ticketsError}`));
  // Open tickets oldest first (cook in order), then the most recent ready ones.
  const open = S.tickets.filter((x) => x.status !== 'ready').sort((a, b) => a.ticket_no - b.ticket_no);
  const ready = S.tickets.filter((x) => x.status === 'ready').slice(0, 12);
  if (!open.length && !ready.length && !S.ticketsError) grid.append(el('p', 'muted-p', 'Köögipileteid pole.'));
  for (const tk of [...open, ...ready]) {
    const done = tk.status === 'ready';
    const card = el('article', `ticket${done ? ' done' : ''}`); card.dataset.ticket = String(tk.ticket_no);
    const mins = Math.max(0, Math.floor((Date.now() - Date.parse(tk.created_at)) / 60000));
    const age = el('small', `ticket-age${!done && mins >= 15 ? ' late' : ''}`, done ? 'VALMIS' : mins < 1 ? 'just nüüd' : `${mins} min`);
    const head = el('div', 'ticket-head'); head.append(el('strong', null, `${tk.table_name} · #${tk.ticket_no}`), age);
    const list = el('ul', 'ticket-list');
    for (const it of tk.lines) list.append(el('li', null, `${it.quantity} × ${it.name}`));
    const b = el('button', `ticket-ready${done ? ' done' : ''}`, done ? 'Valmis ✓' : 'Märgi valmis'); b.type = 'button';
    b.disabled = done;
    if (!done && !can('kitchen.update')) { // only kitchen staff / managers mark tickets ready
      b.hidden = true;
      card.append(el('small', 'muted', 'Valmis märgib köök.'));
    }
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        const { data } = await api(`/api/v1/kitchen/tickets/${tk.id}/ready`, { method: 'POST' }); // idempotent
        S.tickets = S.tickets.map((x) => (x.id === data.id ? data : x));
        announce(`Pilet #${tk.ticket_no} valmis`);
      } catch (e) { S.ticketsError = e.message; }
      renderTicketGrid();
    });
    card.append(head, list, b);
    grid.append(card);
  }
}

const POLL_MS = 4000;
// ---------------------------------------------------------------- realtime
// Server-sent events read with fetch() (EventSource cannot send the Bearer and
// device headers). Frames are invalidations only: on each we re-read the
// affected view through the normal API. If the stream drops we reconnect with
// the last sequence (no event is missed); polling covers the gap.

const LIVE_SAFETY_POLL_MS = 30000;
const LIVE = { ctl: null, cursor: null, connected: false, backoff: 1000, pending: new Set(), timer: null };

function setLive(on) {
  LIVE.connected = on;
  const pill = $('net-status');
  pill.dataset.live = on ? '1' : '0';
  if (S.online) pill.textContent = on ? '● Server ühendatud · reaalajas' : '● Server ühendatud';
}

function stopLive() {
  const ctl = LIVE.ctl; LIVE.ctl = null;
  if (ctl) ctl.abort();
  setLive(false);
}

async function startLive() {
  stopLive();
  const ctl = new AbortController(); LIVE.ctl = ctl;
  let ended = false;
  try {
    const q = LIVE.cursor != null ? `?after=${LIVE.cursor}` : '';
    const res = await fetch(`/api/v1/events/stream${q}`, { headers: authHeaders(), signal: ctl.signal, cache: 'no-store' });
    if (res.status === 401) { if (AUTH.mode === 'server') sessionLost(); return; }
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    setLive(true); LIVE.backoff = 1000;
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) { ended = true; break; }
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) { liveFrame(buf.slice(0, i)); buf = buf.slice(i + 2); }
    }
  } catch { if (ctl.signal.aborted) return; }
  if (LIVE.ctl !== ctl) return;           // replaced or stopped
  setLive(false);
  const wait = ended ? 200 : LIVE.backoff; // normal end (max duration): reconnect at once
  if (!ended) LIVE.backoff = Math.min(LIVE.backoff * 2, 30000);
  setTimeout(() => { if (LIVE.ctl === ctl) startLive(); }, wait);
}

function liveFrame(frame) {
  let type = 'message', id = null, data = null;
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) type = line.slice(6).trim();
    else if (line.startsWith('id:')) id = line.slice(3).trim();
    else if (line.startsWith('data:')) { try { data = JSON.parse(line.slice(5)); } catch { data = null; } }
  }
  if (type === 'auth') { if (AUTH.mode === 'server') sessionLost(); return; }
  if (type === 'hello') {
    if (LIVE.cursor == null && data) LIVE.cursor = data.seq;
    LIVE.pending.add('snapshot');            // (re)connected: re-read what is on screen
  } else if (id && /^\d+$/.test(id)) {
    LIVE.cursor = Number(id);
    if (type.startsWith('order.')) LIVE.pending.add('tables');
    else if (type.startsWith('kitchen.')) { LIVE.pending.add('kitchen'); LIVE.pending.add('tables'); }
    else if (type.startsWith('drawer.') || type.startsWith('pos.')) LIVE.pending.add('drawer');
    else if (type.startsWith('catalog.') || type.startsWith('pricing.')) LIVE.pending.add('catalog');
  }
  clearTimeout(LIVE.timer);
  LIVE.timer = setTimeout(applyLive, 80);   // coalesce bursts
}

async function applyLive() {
  const p = LIVE.pending; LIVE.pending = new Set();
  const all = p.has('snapshot');
  if ((all || p.has('tables')) && S.mode === 'tables') await refreshTables();
  if ((all || p.has('kitchen')) && S.mode === 'kitchen') await loadTickets();
  if (all || p.has('drawer')) await refreshDrawer();
  if ((all || p.has('tables')) && S.bill && !$('modal').hidden && ['input', 'error'].includes(S.bill.state)) await loadBill();
  if (all || p.has('catalog')) {
    try {
      await loadProducts();
      if (S.mode === 'express' && !$('product-grid')?.contains(document.activeElement)) renderExpress();
      if (S.mode === 'express' || S.mode === 'tables') renderBasket();
    } catch { /* the next event or reload retries */ }
  }
}

function startPolling() {
  clearInterval(S.pollTimer);
  S.pollTimer = ['tables', 'kitchen'].includes(S.mode) ? setInterval(poll, POLL_MS) : null;
}
async function poll() {
  if (document.hidden || S.pollBusy) return;
  // With a live stream, polling is only a slow safety net.
  if (LIVE.connected && Date.now() - S.lastPoll < LIVE_SAFETY_POLL_MS) return;
  S.lastPoll = Date.now();
  S.pollBusy = true;
  try {
    if (S.mode === 'tables') await refreshTables();
    else if (S.mode === 'kitchen') await loadTickets();
  } finally { S.pollBusy = false; }
}

async function renderManager() {
  const root = $('view-root'); root.replaceChildren();
  if (AUTH.mode === 'demo') root.append(el('div', 'demo-banner', 'Tagastus kasutab DEMO juhi rolli (päis), mitte päris autentimist.'));
  const stats = el('div', 'stats-grid'); stats.id = 'report';
  const actions = el('div', 'manager-actions');
  const close = el('button', 'secondary-btn', 'Sulge vahetus ja loe raha'); close.type = 'button'; close.id = 'close-drawer';
  close.disabled = !S.drawer;
  close.addEventListener('click', closeDrawerDialog);
  actions.append(close);
  const salesBox = el('section'); salesBox.id = 'sales-list';
  root.append(stats, actions, salesBox);
  stats.append(el('p', 'muted-p', 'Laadin aruannet…'));
  // The tenant-wide report needs a manager/owner; the sales list (for
  // refunds) is loaded independently so a cashier can still request refunds.
  let bdate = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Tallinn' }).format(new Date());
  try {
    if (!can('reports')) throw new ApiError(403, 'forbidden', 'no report permission'); // do not even ask
    const { data: rep } = await api('/api/v1/reports/daily');
    if (S.mode !== 'manager') return;
    bdate = rep.business_date;
    stats.replaceChildren();
    for (const [label, value, note] of [
      ['Müük', formatEuro(rep.total_cents), `${rep.sale_count} tšekki`],
      ['Tagastused', formatEuro(rep.refund_total_cents), `${rep.refund_count} tagastust`],
      ['Sularaha neto', formatEuro(rep.net_cash_cents), 'laekunud − tagastatud'],
      ['Käibemaks neto', formatEuro(rep.net_vat_cents), rep.business_date],
    ]) {
      const d = el('div', 'stat'); d.append(el('small', null, label), el('strong', null, value), el('p', null, note)); stats.append(d);
    }
  } catch (e) {
    if (S.mode !== 'manager') return;
    stats.replaceChildren(el('div', 'pay-state error', e instanceof ApiError && e.status === 403
      ? 'Päevaaruannet näeb ainult juht või omanik.' : `Aruannet ei saanud laadida: ${e.message}`));
  }
  try {
    const { data: sales } = await api(`/api/v1/sales?business_date=${encodeURIComponent(bdate)}`);
    if (S.mode !== 'manager') return;
    salesBox.replaceChildren(el('div', 'section-label', 'Tänased müügid'));
    if (!sales.length) { salesBox.append(el('p', 'muted-p', 'Täna pole veel müüke.')); return; }
    const table = el('table', 'sales-table');
    const thead = el('thead'); const hr = el('tr');
    for (const [h, cls] of [['Tšekk', ''], ['Aeg', ''], ['Kassa', ''], ['Summa', 'num'], ['', '']]) hr.append(el('th', cls, h));
    thead.append(hr);
    const tbody = el('tbody');
    for (const sale of [...sales].reverse()) {
      const tr = el('tr'); tr.dataset.sale = sale.id;
      const time = new Intl.DateTimeFormat('et-EE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Tallinn' }).format(new Date(sale.created_at));
      const reg = S.registers.find((r) => r.id === sale.register_id);
      const b = el('button', null, 'Tagasta…'); b.type = 'button'; b.setAttribute('aria-label', `Tagasta tšekk ${sale.receipt_no}`);
      b.addEventListener('click', () => openRefundDialog(sale.id));
      const td = el('td'); td.append(b);
      tr.append(el('td', null, `#${sale.receipt_no}`), el('td', null, time), el('td', null, reg ? reg.name : String(sale.register_id)), el('td', 'num', formatEuro(sale.total_cents)), td);
      tbody.append(tr);
    }
    table.append(thead, tbody);
    salesBox.append(table);
  } catch (e) {
    salesBox.replaceChildren(el('div', 'pay-state error', `Müüke ei saanud laadida: ${e.message}`));
  }
}

// ----------------------------------------------------------------------- modal

function openModal(build) {
  const overlay = $('modal'); overlay.hidden = false;
  const content = $('modal-content'); content.replaceChildren();
  build(content);
  const focusable = content.querySelector('input, button.primary-btn, button');
  if (focusable) focusable.focus();
}
function closeModal() {
  if (S.payment?.state === 'sending' || S.refund?.state === 'sending' || S.bill?.state === 'sending' || S.cashMove?.state === 'sending') return; // never abandon an in-flight request
  if (S.cashMove && (S.cashMove.state === 'sending' || S.cashMove.state === 'unconfirmed')) return;
  if (S.cashMove && S.cashMove.state !== 'done') S.cashMove = null;
  if (S.bill && S.bill.state !== 'unconfirmed') S.bill = null;
  if (S.recovery) return; // recovery screen is blocking until resolved
  $('modal').hidden = true;
  renderBasket();
  const pay = $('pay-button'); if (!pay.hidden && !pay.disabled) pay.focus();
}
const stateBox = (cls, text) => { const d = el('div', `pay-state ${cls}`, text); d.setAttribute('role', cls === 'error' || cls === 'unconfirmed' ? 'alert' : 'status'); return d; };

// --------------------------------------------------------------------- drawer

function openDrawerDialog() {
  openModal((c) => {
    c.append(el('h2', null, 'Ava kassa')); c.firstChild.id = 'dialog-title';
    const row = el('div', 'form-row');
    const label = el('label', 'section-label', 'Vahetusraha kassas (€)'); label.htmlFor = 'float-input';
    const input = el('input'); input.id = 'float-input'; input.inputMode = 'decimal'; input.value = '50,00';
    row.append(label, input);
    const msg = el('div', 'dialog-error'); msg.setAttribute('role', 'alert');
    const ok = el('button', 'primary-btn', 'Ava kassa'); ok.type = 'button'; ok.id = 'confirm-open-drawer';
    ok.addEventListener('click', async () => {
      const cents = parseEuroInput(input.value);
      if (cents === null) { msg.textContent = 'Sisesta summa, nt 50,00'; return; }
      ok.disabled = true;
      try {
        await api('/api/v1/drawer/open', { method: 'POST', body: { register_id: S.registerId, opening_float_cents: cents } });
        await refreshDrawer();
        closeModal();
        announce('Kassa avatud');
      } catch (e) {
        msg.textContent = e.message; ok.disabled = false;
        if (e instanceof ApiError && e.code === 'drawer_already_open') { await refreshDrawer(); closeModal(); }
      }
    });
    c.append(row, msg, ok);
  });
}

async function closeDrawerDialog() {
  await refreshDrawer();
  if (!S.drawer) return;
  const d = S.drawer;
  openModal((c) => {
    const title = el('h2', null, 'Sulge vahetus'); title.id = 'dialog-title';
    const row = el('div', 'form-row');
    const label = el('label', 'section-label', 'Loetud sularaha (€)'); label.htmlFor = 'counted-input';
    const input = el('input'); input.id = 'counted-input'; input.inputMode = 'decimal';
    row.append(label, input);
    const info = el('p', 'muted', `Server ootab kassas ${formatEuro(d.expected_cents)} (vahetusraha ${formatEuro(d.opening_float_cents)} + müük ${formatEuro(d.cash_sales_cents)} − tagastused ${formatEuro(d.cash_refunds_cents)}).`);
    const msg = el('div', 'dialog-error'); msg.setAttribute('role', 'alert');
    const next = el('button', 'primary-btn', 'Edasi'); next.type = 'button'; next.id = 'close-next';
    next.addEventListener('click', () => {
      const counted = parseEuroInput(input.value);
      if (counted === null) { msg.textContent = 'Sisesta loetud summa, nt 57,95'; return; }
      const preview = counted - d.expected_cents;
      // Second explicit step prevents an accidental close.
      c.replaceChildren(title, el('p', null, `Loetud ${formatEuro(counted)} · oodatud ${formatEuro(d.expected_cents)} · vahe ${formatEuro(preview)}`));
      const confirm = el('button', 'primary-btn', 'Kinnita sulgemine'); confirm.type = 'button'; confirm.id = 'confirm-close';
      const err = el('div', 'dialog-error'); err.setAttribute('role', 'alert');
      const ap = { needed: false, approverId: '', approverPin: '', approvalId: null };
      confirm.addEventListener('click', async () => {
        confirm.disabled = true;
        try {
          const headers = {};
          if (ap.needed) {
            if (!ap.approvalId) {
              const { data: a } = await api('/api/v1/auth/approvals', { method: 'POST', body: {
                action: 'drawer_close', approver_id: ap.approverId, pin: ap.approverPin,
                request: { drawer_id: d.id, counted_cents: counted } } });
              ap.approvalId = a.approval_id; ap.approverPin = '';
            }
            headers['X-Approval-Id'] = ap.approvalId;
          }
          const { data } = await api(`/api/v1/drawer/${d.id}/close`, { method: 'POST', body: { counted_cents: counted }, headers });
          await refreshDrawer();
          c.replaceChildren(title, stateBox(data.variance_cents === 0 ? 'ok' : 'unconfirmed',
            `Kassa suletud. Oodatud ${formatEuro(data.expected_cents)}, loetud ${formatEuro(data.counted_cents)}, vahe ${formatEuro(data.variance_cents)}.`));
          const done = el('button', 'primary-btn', 'Valmis'); done.type = 'button'; done.addEventListener('click', closeModal);
          c.append(done);
          if (S.mode === 'manager') renderManager();
        } catch (e) {
          if (e instanceof ApiError && e.code === 'approval_required' && !ap.needed) {
            // Counted cash differs: a manager approves exactly this count (step-up).
            ap.needed = true;
            c.insertBefore(approvalFields(ap, () => {}), err);
            err.textContent = 'Kassas on vahe – sulgemise peab kinnitama juht oma PIN-iga.';
          } else {
            if (e instanceof ApiError && e.code === 'approval_invalid') ap.approvalId = null;
            err.textContent = e.message;
          }
          confirm.disabled = false;
        }
      });
      c.append(err, confirm);
    });
    c.append(title, info, row, msg, next);
  });
}

async function refreshDrawer() {
  if (!S.registerId) { S.drawer = null; return; }
  try {
    const { data } = await api(`/api/v1/registers/${S.registerId}/drawer`);
    S.drawer = data.drawer;
  } catch (e) {
    // Offline: keep the last known drawer state instead of claiming "closed".
    if (!(e instanceof NetworkError)) S.drawer = null;
    announce(e.message);
  }
  renderDrawerStatus(); renderTotals();
}


function openCashMovementDialog(direction) {
  if (!S.drawer || !can('drawer')) return;
  S.cashMove = { drawerId: S.drawer.id, direction, amount: '',
    reasonCode: direction === 'out' ? 'SAFE_DROP' : 'FLOAT_TOPUP',
    reason: '', approverId: '', approverPin: '', approvalId: null,
    key: null, bodySig: null, state: 'input', error: '' };
  openModal(renderCashMovement);
}

function renderCashMovement(c) {
  const m = S.cashMove; if (!m) return;
  c.replaceChildren();
  const title = el('h2', null, m.direction === 'in' ? 'Sularaha sisse' : 'Sularaha välja / seifi');
  title.id = 'dialog-title';
  c.append(title, el('p', 'muted', 'See ei ole müük. Iga üleandmine lisatakse eraldi kassapäevikusse.'));
  const locked = m.state === 'sending' || m.state === 'unconfirmed';
  const row = el('div', 'form-row');
  const amountLabel = el('label', 'section-label', 'Summa eurodes'); amountLabel.htmlFor = 'cash-move-amount';
  const input = el('input'); input.id = 'cash-move-amount'; input.inputMode = 'decimal'; input.value = m.amount;
  input.disabled = locked;
  input.addEventListener('input', () => { m.amount = input.value; ok.disabled = !valid(); });
  row.append(amountLabel, input); c.append(row);
  const code = el('select'); code.id = 'cash-move-code'; code.disabled = locked;
  for (const [value, label] of [['SAFE_DROP','Seifi üleandmine'], ['FLOAT_TOPUP','Vahetusraha täiendamine'],
                                 ['BANK_DEPOSIT','Panka üleandmine'], ['CASH_CORRECTION','Kassa korrigeerimine'], ['OTHER','Muu']]) {
    const opt = el('option', null, label); opt.value = value; code.append(opt);
  }
  code.value = m.reasonCode;
  code.addEventListener('change', () => { m.reasonCode = code.value; m.approvalId = null; ok.disabled = !valid(); });
  const cl = el('label', 'section-label', 'Põhjus'); cl.htmlFor = 'cash-move-code'; c.append(cl, code);
  const rr = el('div', 'form-row');
  const rl = el('label', 'section-label', 'Kirjeldus (vähemalt 5 märki)'); rl.htmlFor = 'cash-move-reason';
  const reason = el('input'); reason.id = 'cash-move-reason'; reason.maxLength = 300; reason.disabled = locked;
  reason.value = m.reason;
  reason.addEventListener('input', () => { m.reason = reason.value; m.approvalId = null; ok.disabled = !valid(); });
  rr.append(rl, reason); c.append(rr);
  const needsApproval = m.direction === 'out' && AUTH.mode === 'server'
    && !(AUTH.me?.roles || []).some((r) => r === 'manager' || r === 'owner');
  if (needsApproval && !locked) c.append(approvalFields(m, () => { ok.disabled = !valid(); }));
  if (m.state === 'sending') c.append(stateBox('busy', 'Salvestan kassaliikumist…'));
  if (m.state === 'unconfirmed') c.append(stateBox('unconfirmed',
    'Serveri tulemus pole teada. Ära anna raha uuesti välja. Korda sama kirjega.'));
  if (m.state === 'error') c.append(stateBox('error', m.error));
  const ok = el('button', 'primary-btn', m.state === 'unconfirmed' ? 'Kontrolli sama tehingut' : 'Kinnita kassaliikumine');
  ok.type = 'button'; ok.id = 'confirm-cash-movement';
  const valid = () => m.state === 'unconfirmed' || (
    parseEuroInput(m.amount) > 0 && m.reason.trim().length >= 5 &&
    (!needsApproval || Boolean(m.approvalId) || (m.approverId && /^\d{4,8}$/.test(m.approverPin))));
  ok.disabled = m.state === 'sending' || !valid();
  ok.addEventListener('click', submitCashMovement);
  c.append(ok);
}

async function submitCashMovement() {
  const m = S.cashMove; if (!m || m.state === 'sending') return;
  if (!S.online && m.state !== 'unconfirmed') {
    m.state = 'error'; m.error = 'Puudub kinnitatud serveriühendus. Ära liiguta sularaha offline-režiimis.';
    renderCashMovement($('modal-content')); return;
  }
  const amount = parseEuroInput(m.amount);
  if (!(amount > 0) || m.reason.trim().length < 5) return;
  const body = {direction: m.direction, amount_cents: amount,
                reason_code: m.reasonCode, reason_text: m.reason.trim()};
  const bodySig = JSON.stringify(body);
  if (m.state === 'unconfirmed' && bodySig !== m.bodySig) {
    m.error = 'Tundmatu tulemusega tehingut ei tohi muuta'; renderCashMovement($('modal-content')); return;
  }
  if (!m.key || m.bodySig !== bodySig) {
    m.key = newKey(); m.bodySig = bodySig; m.approvalId = null;
  }
  const retry = m.state === 'unconfirmed';
  const needsApproval = m.direction === 'out' && AUTH.mode === 'server'
    && !(AUTH.me?.roles || []).some((r) => r === 'manager' || r === 'owner');
  let headers = {};
  if (needsApproval && !m.approvalId) {
    try {
      const { data } = await api('/api/v1/auth/approvals', {method:'POST', body: {
        action:'drawer_cash_out', approver_id:m.approverId, pin:m.approverPin,
        request:{drawer_id:m.drawerId, ...body}}});
      m.approvalId = data.approval_id;
    } catch (e) { m.error = 'Juhi kinnitust ei saanud: ' + e.message; m.state = retry ? 'unconfirmed' : 'error';
      renderCashMovement($('modal-content')); return; }
    finally { m.approverPin = ''; }
  }
  if (m.approvalId) headers['X-Approval-Id'] = m.approvalId;
  m.state = 'sending'; renderCashMovement($('modal-content'));
  const rec = {op:'cashmove', key:m.key, path:'/api/v1/drawer/' + m.drawerId + '/movements',
    body, headers, registerId:S.registerId, createdAt:new Date().toISOString()};
  const out = await runMoneyOperation(rec, null);
  if (out.blocked) { m.state='error'; m.error=out.blocked; }
  else if (out.data) { m.state='done'; await showCashMovementResult(out.data); return; }
  else if (isUncertain(out.err)) { m.state='unconfirmed'; m.error=out.err.message; }
  else { m.state='error'; m.error=out.err.message; m.key=null; m.bodySig=null; m.approvalId=null; }
  renderCashMovement($('modal-content'));
}

async function showCashMovementResult(data) {
  const c = $('modal-content'); c.replaceChildren();
  const h = el('h2', null, 'Kassaliikumine kinnitatud'); h.id='dialog-title';
  const msg = el('p', null, (data.direction === 'in' ? 'Sisse ' : 'Välja ') + formatEuro(data.amount_cents) +
                ' · ' + data.reason_text);
  const done = el('button', 'primary-btn', 'Valmis'); done.type='button';
  done.addEventListener('click', () => { S.cashMove=null; closeModal(); });
  c.append(h,msg,el('p','muted','Tehingu ID: ' + data.id),done);
  await refreshDrawer();
  done.focus();
}

// -------------------------------------------------------------------- payment

function openPayment() {
  const lines = cartLines();
  if (!lines.length) return;
  const sig = cartSig(lines);
  if (!(S.payment && S.payment.sig === sig && S.payment.state === 'unconfirmed')) {
    if (!S.quote || S.quoteSig !== sig) return;
    const order = S.mode === 'tables' ? S.order : null;
    if (S.mode === 'tables' && (!order || S.orderBusy)) return;
    S.payment = { sig, cartKey: cartKey(), lines: requestLines(lines), orderId: order?.id || null, version: order?.version || null,
      due: S.quote.cash_due_cents, key: null, bodySig: null, tendered: S.quote.cash_due_cents, state: 'input', error: '', sale: null };
  }
  openModal(renderPayment);
}

function renderPayment(c) {
  const p = S.payment;
  c.replaceChildren();
  const title = el('h2', null, 'Sularahamakse'); title.id = 'dialog-title';
  c.append(title, el('p', 'muted', 'Summad arvutab AXAR POS server. Simuleeritud sularaha – päris raha ei liigu.'));
  const amount = el('div', 'amount', formatEuro(p.due)); amount.id = 'payment-due';
  c.append(amount);
  const locked = p.state === 'sending' || p.state === 'unconfirmed';
  const row = el('div', 'form-row');
  const label = el('label', 'section-label', 'Kliendilt saadud (€)'); label.htmlFor = 'tender-input';
  const input = el('input', 'tender-input'); input.id = 'tender-input'; input.inputMode = 'decimal'; input.value = centsToInput(p.tendered); input.disabled = locked;
  row.append(label, input);
  const quick = el('div', 'quick-amounts');
  for (const n of [10, 20, 50]) {
    const b = el('button', null, `${n} €`); b.type = 'button'; b.disabled = locked;
    b.addEventListener('click', () => { input.value = `${n},00`; update(); });
    quick.append(b);
  }
  const change = el('div', 'summary-line');
  const changeValue = el('strong'); changeValue.id = 'change-preview';
  change.append(el('span', null, 'Tagastusraha (eelvaade)'), changeValue);
  const msg = el('div', 'dialog-error'); msg.id = 'payment-error'; msg.setAttribute('role', 'alert');
  const confirm = el('button', 'primary-btn'); confirm.type = 'button'; confirm.id = 'confirm-payment';
  c.append(row, quick, change, msg);

  if (p.state === 'sending') c.append(stateBox('busy', 'Saadan makset serverisse…'));
  if (p.state === 'unconfirmed') {
    c.append(stateBox('unconfirmed', `Makse olek on teadmata (${p.error}). Server võis makse juba salvestada. Ära võta raha uuesti vastu ega alusta uut müüki – vajuta „Proovi uuesti“. Sama makset ei registreerita kaks korda.`));
  }
  if (p.state === 'error') c.append(stateBox('error', `Makset ei salvestatud: ${p.error}`));
  confirm.textContent = p.state === 'unconfirmed' ? 'Proovi uuesti (sama makse)' : p.state === 'sending' ? 'Saadan…' : 'Kinnita makse';
  confirm.disabled = p.state === 'sending';
  c.append(confirm);

  function update() {
    if (locked) { changeValue.textContent = formatEuro(p.tendered - p.due); return; }
    const cents = parseEuroInput(input.value);
    if (cents === null) { changeValue.textContent = '—'; msg.textContent = 'Sisesta korrektne summa, nt 10,00'; confirm.disabled = true; return; }
    p.tendered = cents;
    if (cents < p.due) { changeValue.textContent = '—'; msg.textContent = 'Sularahast ei piisa.'; confirm.disabled = true; return; }
    changeValue.textContent = formatEuro(cents - p.due); msg.textContent = ''; confirm.disabled = false;
  }
  input.addEventListener('input', update);
  confirm.addEventListener('click', submitPayment);
  update();
}

async function submitPayment() {
  const p = S.payment;
  if (!p || p.state === 'sending' || p.state === 'done') return; // double-tap guard
  // A table bill is paid by order id + the version the cashier saw: the server
  // takes the lines from the order and closes it in the same transaction.
  const body = p.orderId
    ? { version: p.version, register_id: S.registerId, tendered_cents: p.tendered }
    : { register_id: S.registerId, lines: p.lines, tendered_cents: p.tendered };
  const path = p.orderId ? `/api/v1/orders/${p.orderId}/pay-cash` : '/api/v1/sales/cash';
  const bodySig = JSON.stringify([path, body]);
  if (p.state === 'unconfirmed') {
    // Retry: must be byte-for-byte the same request with the same key.
    if (bodySig !== p.bodySig) { p.error = 'muutunud makset ei saa korrata'; renderPayment($('modal-content')); return; }
  } else if (!p.key || p.bodySig !== bodySig) {
    p.key = newKey(); p.bodySig = bodySig;
  }
  const retry = p.state === 'unconfirmed';
  p.state = 'sending'; p.error = ''; // set before awaiting the lock: double-tap guard
  renderPayment($('modal-content'));
  const rec = { op: 'sale', key: p.key, path, body, headers: {}, cartKey: p.cartKey, createdAt: new Date().toISOString() };
  const out = await runMoneyOperation(rec, () => clearPaidCart(p.cartKey));
  if (out.blocked) {
    p.error = out.blocked;
    if (retry) p.state = 'unconfirmed'; else { p.state = 'error'; p.key = null; p.bodySig = null; }
  } else if (out.data) {
    p.state = 'done'; p.sale = out.data;
    await showReceipt(out.data, out.cleaned ? '' : CLEANUP_WARNING);
    refreshDrawer();
    return;
  } else if (isUncertain(out.err)) {
    p.state = 'unconfirmed'; p.error = out.err.message; // journal stays
  } else {
    p.state = 'error'; p.error = out.err.message; p.key = null; p.bodySig = null;
    if (out.err.code === 'no_open_drawer') refreshDrawer();
    if (out.err.code === 'order_changed') {
      p.error = 'arvet muudeti teises seadmes või see on juba tasutud – kontrolli arvet ja alusta maksmist uuesti';
      reloadOrder().then(refreshTableView);
    }
  }
  if (!$('modal').hidden) renderPayment($('modal-content'));
  renderTotals();
}


// --------------------------------------------------- table bill payments (C3)
// One bill, many tenders: cash, card (SIMULATED terminal) and bank transfer
// (a receivable until the bank statement matches it). The server owns every
// amount; the device sends the total it showed and the server refuses a bill
// that changed meanwhile. Each tender goes through the same crash-safe
// journal + Idempotency-Key as a sale.

const METHOD_ET = { cash: 'Sularaha', card: 'Kaart', transfer: 'Ülekanne' };
const PAY_STATUS_ET = { captured: 'laekunud', pending: 'ootab panka', unknown: 'teadmata', declined: 'keeldutud' };
const roundCash5 = (c) => Math.floor((2 * c + 5) / 10) * 5; // == server round_cash(c, 5), c >= 0

async function openBillPayment() {
  const order = S.order;
  if (S.mode !== 'tables' || !order || S.orderBusy) return;
  if (!(S.bill && S.bill.orderId === order.id && S.bill.state === 'unconfirmed')) {
    S.bill = { orderId: order.id, cartKey: cartKey(), data: null, method: 'cash', amount: null, tendered: null,
      simulate: 'approved', customer: '', key: null, bodySig: null, body: null, state: 'loading', error: '', note: '' };
  }
  openModal(renderBill);
  await loadBill();
}

async function loadBill() {
  const b = S.bill; if (!b) return;
  try {
    const { data } = await api(`/api/v1/orders/${b.orderId}/bill`);
    if (S.bill !== b) return;
    b.data = data;
    if (b.state === 'loading') b.state = 'input';
    if (b.state === 'input' && (b.amount == null || b.amount > data.remaining_cents)) b.amount = data.remaining_cents;
  } catch (e) {
    if (S.bill !== b) return;
    if (b.state !== 'unconfirmed') { b.state = 'error'; b.error = e.message; }
  }
  if (!$('modal').hidden && S.bill === b && b.state !== 'sending') renderBill($('modal-content'));
}

function billDue(b) {
  const d = b.data;
  if (b.method !== 'cash' || !d) return b.amount;
  const final = b.amount === d.remaining_cents && d.in_flight_cents === 0;
  return final ? roundCash5(b.amount) : b.amount;
}

function renderBill(c) {
  const b = S.bill;
  c.replaceChildren();
  const title = el('h2', null, 'Arve tasumine'); title.id = 'dialog-title';
  c.append(title, el('p', 'muted', 'Summad arvutab server. Kaart ja pank on SIMULEERITUD – päris raha ei liigu.'));
  const d = b.data;
  if (!d) { c.append(b.state === 'error' ? stateBox('error', `Arvet ei saanud laadida: ${b.error}`) : stateBox('busy', 'Laadin arvet…')); return; }
  const sums = el('div', 'bill-sums'); sums.id = 'bill-sums';
  const sum = (id, label, cents, cls = '') => {
    const row = el('div', `summary-line ${cls}`); const v = el('strong', null, formatEuro(cents)); v.id = id;
    row.append(el('span', null, label), v); sums.append(row);
  };
  sum('bill-total', 'Arve kokku', d.total_cents);
  sum('bill-paid', 'Tasutud', d.paid_cents);
  if (d.pending_transfer_cents) sum('bill-pending', 'Ülekandega (ootab panka)', d.pending_transfer_cents);
  if (d.in_flight_cents) sum('bill-inflight', 'Kaardimakse olek teadmata', d.in_flight_cents, 'bad');
  sum('bill-remaining', 'Jääk tasuda', d.remaining_cents, 'grand');
  c.append(sums);

  const list = el('ul', 'bill-payments'); list.id = 'bill-payments';
  for (const p of d.payments) {
    const li = el('li', `pay-${p.status}`);
    li.append(el('span', null, `${METHOD_ET[p.method]} ${formatEuro(p.amount_cents)}${p.rounding_cents ? ` (ümardus ${formatEuro(p.rounding_cents)})` : ''}`),
      el('span', 'muted', PAY_STATUS_ET[p.status] || p.status));
    if (p.method === 'card' && p.status === 'unknown') {
      const rb = el('button', 'secondary-btn', 'Küsi terminalist'); rb.type = 'button'; rb.dataset.reconcile = p.id;
      rb.addEventListener('click', () => reconcileCard(p.id, rb));
      li.append(rb);
    }
    list.append(li);
  }
  if (d.payments.length) c.append(el('div', 'section-label', 'Maksed'), list);

  if (d.status !== 'open' && d.status !== 'partially_paid') {
    c.append(stateBox('ok', d.status === 'invoiced' ? 'Arve on tasutud; ülekande osa ootab panka.' : 'Arve on tasutud.'));
    return;
  }
  if (d.in_flight_cents) c.append(stateBox('unconfirmed', 'Kaardimakse vastus jäi saamata. Ära küsi kliendilt sama summat uuesti – vajuta „Küsi terminalist“.'));
  if (d.status === 'partially_paid') c.append(el('p', 'muted', 'Osaliselt tasutud arve ridu enam ei muudeta.'));

  const locked = b.state === 'sending' || b.state === 'unconfirmed';
  const methods = el('div', 'method-tabs'); methods.setAttribute('role', 'group'); methods.setAttribute('aria-label', 'Makseviis');
  for (const m of ['cash', 'card', 'transfer']) {
    const mb = el('button', m === b.method ? 'active' : '', METHOD_ET[m]); mb.type = 'button'; mb.id = `method-${m}`;
    mb.setAttribute('aria-pressed', String(m === b.method)); mb.disabled = locked;
    mb.addEventListener('click', () => { b.method = m; renderBill(c); });
    methods.append(mb);
  }
  c.append(methods);

  const row = el('div', 'form-row');
  const lab = el('label', 'section-label', 'Summa (€)'); lab.htmlFor = 'bill-amount';
  const amount = el('input', 'tender-input'); amount.id = 'bill-amount'; amount.inputMode = 'decimal';
  amount.value = centsToInput(b.amount ?? d.remaining_cents); amount.disabled = locked;
  row.append(lab, amount);
  const quick = el('div', 'quick-amounts');
  const half = Math.floor(d.remaining_cents / 2 / 5) * 5;
  for (const [label, cents] of [['Kogu jääk', d.remaining_cents], ['Pool', half]]) {
    if (cents <= 0) continue;
    const qb = el('button', null, label); qb.type = 'button'; qb.disabled = locked;
    qb.addEventListener('click', () => { amount.value = centsToInput(cents); update(); });
    quick.append(qb);
  }
  c.append(row, quick);

  let tender = null, change = null, cust = null, sim = null;
  if (b.method === 'cash') {
    const tr = el('div', 'form-row');
    const tl = el('label', 'section-label', 'Kliendilt saadud (€)'); tl.htmlFor = 'bill-tendered';
    tender = el('input', 'tender-input'); tender.id = 'bill-tendered'; tender.inputMode = 'decimal'; tender.disabled = locked;
    tender.value = centsToInput(b.tendered ?? billDue(b));
    tr.append(tl, tender);
    const cl = el('div', 'summary-line'); change = el('strong'); change.id = 'bill-change';
    cl.append(el('span', null, 'Tagastusraha'), change);
    c.append(tr, cl);
  } else if (b.method === 'card') {
    const sr = el('div', 'form-row');
    const sl = el('label', 'section-label', 'DEMO terminali vastus'); sl.htmlFor = 'bill-simulate';
    sim = el('select'); sim.id = 'bill-simulate'; sim.disabled = locked;
    for (const [v, txt] of [['approved', 'Kinnitatud'], ['declined', 'Keeldutud'], ['timeout', 'Vastus kadus (side katkes)']]) {
      const o = el('option', null, txt); o.value = v; o.selected = v === b.simulate; sim.append(o);
    }
    sim.addEventListener('change', () => { b.simulate = sim.value; });
    sr.append(sl, sim); c.append(sr);
  } else {
    const cr = el('div', 'form-row');
    const cl = el('label', 'section-label', 'Maksja (ettevõte või isik)'); cl.htmlFor = 'bill-customer';
    cust = el('input'); cust.id = 'bill-customer'; cust.autocomplete = 'off'; cust.value = b.customer; cust.disabled = locked;
    cr.append(cl, cust);
    c.append(cr, el('p', 'muted', 'Server koostab arve viitenumbriga. Summa loetakse laekunuks alles pangaväljavõtte sobitamisel.'));
  }
  const msg = el('div', 'dialog-error'); msg.id = 'bill-error'; msg.setAttribute('role', 'alert');
  c.append(msg);
  if (b.note) c.append(stateBox('ok', b.note));
  if (b.state === 'sending') c.append(stateBox('busy', 'Saadan makse serverisse…'));
  if (b.state === 'unconfirmed') c.append(stateBox('unconfirmed', `Makse olek on teadmata (${b.error}). Server võis selle juba salvestada – ära võta raha uuesti, vajuta „Proovi uuesti“. Sama makset ei registreerita kaks korda.`));
  if (b.state === 'error' && b.error) c.append(stateBox('error', `Makset ei salvestatud: ${b.error}`));
  const confirm = el('button', 'primary-btn'); confirm.type = 'button'; confirm.id = 'bill-confirm';
  confirm.textContent = b.state === 'unconfirmed' ? 'Proovi uuesti (sama makse)' : b.state === 'sending' ? 'Saadan…'
    : `Registreeri ${METHOD_ET[b.method].toLowerCase()}`;
  c.append(confirm);

  function update() {
    if (locked) { confirm.disabled = b.state === 'sending'; return; }
    const cents = parseEuroInput(amount.value);
    confirm.disabled = true;
    if (cents === null || cents <= 0) { msg.textContent = 'Sisesta korrektne summa, nt 20,00'; return; }
    if (cents > d.remaining_cents) { msg.textContent = `Summa ületab jäägi (${formatEuro(d.remaining_cents)}).`; return; }
    b.amount = cents;
    if (b.method === 'cash') {
      const final = cents === d.remaining_cents && d.in_flight_cents === 0;
      if (!final && cents % 5) { msg.textContent = 'Sularaha osamakse peab olema 5 sendi kordne.'; change.textContent = '—'; return; }
      const due = billDue(b);
      const tc = parseEuroInput(tender.value);
      if (tc === null) { msg.textContent = 'Sisesta saadud summa'; change.textContent = '—'; return; }
      b.tendered = tc;
      if (tc < due) { msg.textContent = `Sularahast ei piisa (vaja ${formatEuro(due)}).`; change.textContent = '—'; return; }
      change.textContent = formatEuro(tc - due);
    }
    if (b.method === 'transfer') {
      b.customer = cust.value;
      if (cust.value.trim().length < 2) { msg.textContent = 'Sisesta maksja nimi.'; return; }
    }
    msg.textContent = ''; confirm.disabled = false;
  }
  amount.addEventListener('input', () => { if (b.method === 'cash' && tender) { const x = parseEuroInput(amount.value); if (x !== null) { b.amount = x; tender.value = centsToInput(billDue(b)); } } update(); });
  tender?.addEventListener('input', update);
  cust?.addEventListener('input', update);
  confirm.addEventListener('click', submitBillPayment);
  update();
}

async function submitBillPayment() {
  const b = S.bill;
  if (!b || !b.data || b.state === 'sending') return; // double-tap guard
  let body;
  if (b.state === 'unconfirmed') body = b.body;         // retry: byte-for-byte the same request
  else {
    body = { method: b.method, register_id: S.registerId, expected_total_cents: b.data.total_cents, amount_cents: b.amount };
    if (b.method === 'cash') body.tendered_cents = b.tendered;
    if (b.method === 'card') body.simulate = b.simulate;
    if (b.method === 'transfer') body.customer_name = b.customer.trim();
    const sig = JSON.stringify(body);
    if (!b.key || b.bodySig !== sig) { b.key = newKey(); b.bodySig = sig; }
    b.body = body;
  }
  const retry = b.state === 'unconfirmed';
  b.state = 'sending'; b.error = ''; b.note = '';
  renderBill($('modal-content'));
  const path = `/api/v1/orders/${b.orderId}/payments`;
  const rec = { op: 'billpay', key: b.key, path, body, headers: {}, cartKey: b.cartKey, createdAt: new Date().toISOString() };
  const out = await runMoneyOperation(rec, null);
  if (out.blocked) {
    b.error = out.blocked;
    if (retry) b.state = 'unconfirmed'; else { b.state = 'error'; b.key = null; b.bodySig = null; }
  } else if (out.data) {
    b.key = null; b.bodySig = null; b.body = null;
    await billPaid(b.orderId, out.data);
    return;
  } else if (isUncertain(out.err)) {
    b.state = 'unconfirmed'; b.error = out.err.message;   // journal stays
  } else {
    b.state = 'error'; b.error = out.err.message; b.key = null; b.bodySig = null;
    if (out.err.code === 'no_open_drawer') refreshDrawer();
    if (out.err.code === 'bill_changed') b.error = 'arvet muudeti teises seadmes – kontrolli summat ja proovi uuesti';
    if (['bill_changed', 'overpayment', 'bill_closed'].includes(out.err.code)) { b.state = 'input'; b.note = ''; await loadBill(); b.state = 'error'; b.error = b.error || out.err.message; }
  }
  if (!$('modal').hidden && S.bill === b) renderBill($('modal-content'));
  renderTotals();
}

/** After a recorded tender: show the receipt when the bill is settled, else the bill. */
async function billPaid(orderId, data, note = '') {
  const { payment, bill } = data;
  refreshDrawer();
  if (bill.sale_id) {
    clearPaidCart(`order:${orderId}`);
    S.bill = null;
    try {
      const { data: sale } = await api(`/api/v1/sales/${bill.sale_id}`);
      if ($('modal').hidden) openModal(() => {});
      await showReceipt(sale, bill.status === 'invoiced' ? 'Ülekande osa on arvel ja ootab panka.' : '');
    } catch (e) {
      openModal((c) => c.replaceChildren(stateBox('ok', `Arve on tasutud (tšeki laadimine ebaõnnestus: ${e.message}).`)));
    }
    return;
  }
  if (!S.bill || S.bill.orderId !== orderId) {
    S.bill = { orderId, cartKey: `order:${orderId}`, data: null, method: 'cash', amount: null, tendered: null,
      simulate: 'approved', customer: '', key: null, bodySig: null, body: null, state: 'input', error: '', note: '' };
  }
  const b = S.bill;
  b.data = bill; b.state = 'input'; b.amount = bill.remaining_cents; b.tendered = null;
  b.note = note || (payment.status === 'declined' ? '' : `${METHOD_ET[payment.method]} ${formatEuro(payment.amount_cents)} registreeritud.`);
  if (payment.status === 'declined') { b.state = 'error'; b.error = 'kaart keeldus – küsi teist makseviisi'; }
  if ($('modal').hidden) openModal(renderBill); else renderBill($('modal-content'));
  announce(b.note || 'Kaart keeldus');
  refreshTables().then(() => renderBasket());   // bill status + table card (also without the live stream)
}

async function reconcileCard(paymentId, btn) {
  btn.disabled = true; btn.textContent = 'Küsin…';
  try {
    const { data } = await api(`/api/v1/payments/${paymentId}/reconcile`, { method: 'POST' });
    const p = data.payments.find((x) => x.id === paymentId);
    await billPaid(S.bill.orderId, { payment: p, bill: data },
      p.status === 'captured' ? 'Terminal kinnitas: kaardimakse laekus.' : '');
    if (p.status === 'declined' && S.bill) { S.bill.state = 'error'; S.bill.error = 'terminali andmetel makset ei toimunud – küsi uuesti'; renderBill($('modal-content')); }
  } catch (e) {
    btn.disabled = false; btn.textContent = 'Küsi terminalist';
    if (S.bill) { S.bill.error = e.message; S.bill.state = 'error'; renderBill($('modal-content')); }
  }
}

const CLEANUP_WARNING = 'Makse on serveris salvestatud, aga seadme olekut ei õnnestunud puhastada (salvestus ei tööta). Kassa on lukus; pärast salvestuse taastumist laadi leht uuesti – sama makset ei registreerita kaks korda.';

/**
 * One money operation (sale/refund/recovery) under an origin-wide Web Lock:
 * journal check+write → POST (same key) → local cleanup → journal delete.
 * The journal is deleted ONLY after a definitive answer AND successful cleanup,
 * so a crash at any point leaves a record that a same-key retry resolves.
 * Returns {blocked} | {data, cleaned} | {err}.
 */
async function runMoneyOperation(rec, cleanup) {
  return withMoneyLock(async () => {
    const blocked = guardJournal(rec.key, rec);
    if (blocked) return { blocked };
    try {
      const { data } = await api(rec.path, { method: 'POST', body: rec.body, headers: { 'Idempotency-Key': rec.key, ...(rec.headers || {}) } });
      const cleaned = cleanup ? cleanup() !== false : true;
      if (cleaned) clearPending(rec.key); else { S.storageOk = false; renderTotals(); }
      return { data, cleaned };
    } catch (err) {
      if (!isUncertain(err)) clearPending(rec.key); // proven not recorded
      return { err };
    }
  });
}

async function withMoneyLock(fn) {
  if (!S.locksOk) return { blocked: 'brauser ei toeta akende lukustust – makseid ei saa turvaliselt teha' };
  let out = null;
  try {
    await navigator.locks.request('axar-one-money', { ifAvailable: true }, async (lock) => { if (lock) out = await fn(); });
  } catch (e) { return { blocked: `lukustus ebaõnnestus (${e.message})` }; }
  return out ?? { blocked: 'teises aknas on rahatehing pooleli – oota, kuni see lõpeb' };
}

/** Writes the journal record; returns an error text if sending must be blocked. */
function guardJournal(key, rec) {
  const cur = readPending();
  if (cur.state === 'corrupt' || cur.state === 'unreadable') return 'seadmes on loetamatu pooleli tehing – laadi leht uuesti';
  if (cur.state === 'ok' && cur.rec.key !== key) return 'teises aknas või varem on pooleli tehing – lõpeta see enne';
  try { writePending({ ...rec, registerId: rec.registerId || rec.body.register_id }); } catch {
    S.storageOk = false; renderTotals();
    return 'seadme salvestus ei tööta, tehingut ei saadetud (topelttehingu oht)';
  }
  return null;
}

function clearPaidCart(key) {
  if (key.startsWith('order:')) {
    // The server closed the bill in the payment transaction: nothing durable
    // is kept on this device, we only drop our view of it.
    const id = key.slice('order:'.length);
    if (S.order?.id === id) { S.order = null; S.tableId = null; S.orderGen++; }
    S.tables.forEach((tb) => { if (tb.open_order?.id === id) tb.open_order = null; });
    loadTables().then(() => { if (S.mode === 'tables') renderTableGrid(); });
  } else if (key.startsWith('walkin:')) {
    S.walkin[key.split(':')[1]] = {};
  } // legacy 'table:' keys (device-local demo bills of PR #12): nothing left to clean
  S.quote = null; S.quoteSig = '';
  if (S.mode === 'tables') renderTableGrid();
  renderBasket();
  return true;
}

async function showReceipt(sale, warning = '') {
  const c = $('modal-content');
  c.replaceChildren();
  const title = el('h2', null, 'Makse kinnitatud'); title.id = 'dialog-title';
  const change = el('div', 'amount', `Tagasi ${formatEuro(sale.change_cents)}`); change.id = 'change-due';
  const receipt = el('pre', 'receipt', 'Laadin tšekki…'); receipt.id = 'receipt';
  const done = el('button', 'primary-btn', 'Järgmine klient'); done.type = 'button'; done.id = 'next-customer';
  done.addEventListener('click', () => { S.payment = null; closeModal(); });
  c.append(title, el('p', 'muted', `Tšekk nr ${sale.receipt_label || sale.receipt_no} · server kinnitas makse.`), change);
  if (warning) c.append(stateBox('unconfirmed', warning));
  c.append(receipt, done);
  announce(`Makse kinnitatud. Tagasi ${formatEuro(sale.change_cents)}`);
  try {
    const { data } = await api(`/api/v1/sales/${sale.id}/receipt.txt`);
    receipt.textContent = data;
  } catch (e) {
    receipt.textContent = `Makse on salvestatud, aga tšeki laadimine ebaõnnestus (${e.message}). Tšeki saab hiljem uuesti avada juhi vaatest.`;
  }
  done.focus();
}

// --------------------------------------------------------------------- tables

/** Idempotent per key: a retry after a lost answer re-sends the SAME key, so
 * the kitchen never gets the same ticket twice. */
function sendToKitchen() {
  if (S.mode !== 'tables' || !S.order) return;
  const gen = S.orderGen;
  S.orderQueue = S.orderQueue.then(async () => {
    const o = S.order;
    if (gen !== S.orderGen || !o || paymentLocked()) return;
    if (!S.kitchenPending || S.kitchenPending.orderId !== o.id || S.kitchenPending.version !== o.version) {
      S.kitchenPending = { orderId: o.id, version: o.version, key: newKey() };
    }
    const kp = S.kitchenPending;
    S.orderBusy = true; renderBasket();
    try {
      const { data } = await api(`/api/v1/orders/${o.id}/kitchen`, { method: 'POST', body: { version: kp.version }, headers: { 'Idempotency-Key': kp.key } });
      S.kitchenPending = null;
      if (S.order?.id === data.order.id) S.order = data.order;
      S.orderBusy = false;
      setOrderMessage(`Köögipilet #${data.ticket.ticket_no} saadetud; arve jäi avatuks.`, 'ok');
    } catch (e) {
      S.orderBusy = false;
      if (e instanceof ApiError && ['order_changed', 'nothing_to_send', 'order_not_open'].includes(e.code)) {
        S.kitchenPending = null; S.orderGen++;
        await reloadOrder();
        if (e.code !== 'order_not_open') setOrderMessage(e.code === 'nothing_to_send' ? 'Kõik tooted on juba kööki saadetud.' : 'Arvet muudeti teises seadmes – kontrolli arvet ja saada uuesti.', 'error');
      } else {
        // Unknown outcome: keep the key, the retry cannot create a second ticket.
        setOrderMessage(`Köögipileti saatmine ebaõnnestus (${e.message}). Proovi uuesti – topeltpiletit ei teki.`, 'error');
      }
    } finally { S.orderBusy = false; }
    refreshTableView();
  }).catch(() => { S.orderBusy = false; });
}

// -------------------------------------------------------------------- refunds

async function openRefundDialog(saleId) {
  let sale;
  try { ({ data: sale } = await api(`/api/v1/sales/${saleId}`)); } catch (e) { announce(e.message); return; }
  S.refund = { saleId, sale, qty: {}, reason: '', key: null, bodySig: null, state: 'input', error: '', result: null,
    approverId: '', approverPin: '', approvalId: null };
  openModal(renderRefund);
}

function renderRefund(c) {
  const r = S.refund;
  c.replaceChildren();
  const title = el('h2', null, `Tagastus · tšekk #${r.sale.receipt_no}`); title.id = 'dialog-title';
  c.append(title, el('p', 'muted', AUTH.mode === 'server'
    ? 'Simuleeritud sularaha tagastus. Tagastuse kinnitab teine juht oma PIN-iga.'
    : 'Simuleeritud sularaha tagastus. Juhi roll on DEMO (päis), mitte päris autentimine.'));
  const locked = r.state === 'sending' || r.state === 'unconfirmed';
  for (const ln of r.sale.lines) {
    const remaining = ln.quantity - (ln.refunded_quantity || 0);
    const row = el('div', 'refund-line');
    const info = el('div'); info.append(el('strong', null, ln.name), el('br'), el('small', null, `Müüdud ${ln.quantity} · tagastatav ${remaining}`));
    const ctr = el('div', 'cart-controls');
    const n = r.qty[ln.line_no] || 0;
    const minus = el('button', 'qty-btn', '−'); minus.type = 'button'; minus.disabled = locked || n <= 0; minus.setAttribute('aria-label', `Vähenda tagastust: ${ln.name}`);
    const plus = el('button', 'qty-btn', '+'); plus.type = 'button'; plus.disabled = locked || n >= remaining; plus.setAttribute('aria-label', `Tagasta veel: ${ln.name}`);
    plus.dataset.line = String(ln.line_no);
    minus.addEventListener('click', () => { r.qty[ln.line_no] = n - 1; renderRefund(c); });
    plus.addEventListener('click', () => { r.qty[ln.line_no] = n + 1; renderRefund(c); });
    ctr.append(minus, el('span', 'qty-count', String(n)), plus);
    row.append(info, ctr);
    c.append(row);
  }
  const fr = el('div', 'form-row');
  const label = el('label', 'section-label', 'Põhjus (kohustuslik)'); label.htmlFor = 'refund-reason';
  const reason = el('input'); reason.id = 'refund-reason'; reason.value = r.reason; reason.disabled = locked; reason.maxLength = 500;
  reason.addEventListener('input', () => { r.reason = reason.value; ok.disabled = !valid(); });
  fr.append(label, reason);
  c.append(fr);
  if (AUTH.mode === 'server' && !locked) c.append(approvalFields(r, () => { ok.disabled = !valid(); }));
  if (r.state === 'sending') c.append(stateBox('busy', 'Saadan tagastust serverisse…'));
  if (r.state === 'unconfirmed') c.append(stateBox('unconfirmed', `Tagastuse olek on teadmata (${r.error}). Ära maksa raha uuesti välja – vajuta „Proovi uuesti“.`));
  if (r.state === 'error') c.append(stateBox('error', `Tagastust ei salvestatud: ${r.error}`));
  const ok = el('button', 'primary-btn', r.state === 'unconfirmed' ? 'Proovi uuesti (sama tagastus)' : 'Kinnita tagastus'); ok.type = 'button'; ok.id = 'confirm-refund';
  const valid = () => r.state === 'unconfirmed' || (Object.values(r.qty).some((q) => q > 0) && r.reason.trim().length >= 3
    && (AUTH.mode !== 'server' || Boolean(r.approvalId) || (r.approverId && /^\d{4,8}$/.test(r.approverPin))));
  ok.disabled = r.state === 'sending' || !valid();
  ok.addEventListener('click', submitRefund);
  c.append(ok);
}

async function submitRefund() {
  const r = S.refund;
  if (!r || r.state === 'sending' || r.state === 'done') return;
  const lines = Object.entries(r.qty).filter(([, q]) => q > 0).map(([n, q]) => ({ line_no: Number(n), quantity: q }));
  const body = { register_id: S.registerId, lines, reason: r.reason.trim() };
  const bodySig = JSON.stringify(body);
  if (r.state === 'unconfirmed') {
    if (bodySig !== r.bodySig) { r.error = 'muutunud tagastust ei saa korrata'; renderRefund($('modal-content')); return; }
  } else if (!r.key || r.bodySig !== bodySig) { r.key = newKey(); r.bodySig = bodySig; r.approvalId = null; }
  const retry = r.state === 'unconfirmed';
  r.state = 'sending'; r.error = '';
  renderRefund($('modal-content'));
  let headers = DEMO_MANAGER;
  if (AUTH.mode === 'server') {
    // Step-up: a DIFFERENT manager approves exactly this refund with their PIN.
    if (!r.approvalId) {
      try {
        const { data } = await api('/api/v1/auth/approvals', { method: 'POST', body: {
          action: 'refund', approver_id: r.approverId, pin: r.approverPin,
          request: { sale_id: r.saleId, ...body } } });
        r.approvalId = data.approval_id;
      } catch (e) {
        r.state = retry ? 'unconfirmed' : 'error'; r.error = `juhi kinnitus ebaõnnestus: ${e.message}`;
        if (!retry) { r.key = null; r.bodySig = null; }
        renderRefund($('modal-content')); return;
      } finally { r.approverPin = ''; }
    }
    headers = { 'X-Approval-Id': r.approvalId };
  }
  const rec = { op: 'refund', key: r.key, path: `/api/v1/sales/${r.saleId}/refunds`, body, headers, saleId: r.saleId, createdAt: new Date().toISOString() };
  const out = await runMoneyOperation(rec, null);
  if (out.blocked) {
    r.error = out.blocked;
    if (retry) r.state = 'unconfirmed'; else { r.state = 'error'; r.key = null; r.bodySig = null; }
  } else if (out.data) {
    r.state = 'done'; r.result = out.data;
    await showRefundResult(out.data);
    return;
  } else if (isUncertain(out.err)) { r.state = 'unconfirmed'; r.error = out.err.message; } // journal stays
  else { r.state = 'error'; r.error = out.err.message; r.key = null; r.bodySig = null; r.approvalId = null; }
  renderRefund($('modal-content'));
}

async function showRefundResult(data) {
  const c = $('modal-content'); c.replaceChildren();
  const title = el('h2', null, 'Tagastus kinnitatud'); title.id = 'dialog-title';
  const amount = el('div', 'amount', `Välja ${formatEuro(data.cash_paid_cents)}`); amount.id = 'refund-paid';
  const receipt = el('pre', 'receipt', 'Laadin tagastuse tšekki…'); receipt.id = 'refund-receipt';
  const done = el('button', 'primary-btn', 'Valmis'); done.type = 'button';
  done.addEventListener('click', () => { S.refund = null; closeModal(); if (S.mode === 'manager') renderManager(); });
  c.append(title, amount, receipt, done);
  refreshDrawer();
  try { receipt.textContent = (await api(`/api/v1/refunds/${data.id}/receipt.txt`)).data; } catch (e) { receipt.textContent = `Tagastus on salvestatud; tšeki laadimine ebaõnnestus (${e.message}).`; }
}

// -------------------------------------------------------------- recovery

/** Blocking screen for a persisted pending operation found on boot or from
 * another tab. The only ways out are a definitive server answer for the SAME
 * key and body, or (for an unreadable record) an explicit two-step discard. */
function showRecovery(found) {
  S.recovery = found;
  $('close-modal').hidden = true;
  openModal((c) => renderRecovery(c));
  renderTotals();
  if (found.state === 'ok') {
    api(`/api/v1/operations/${encodeURIComponent(found.rec.key)}`).then(({ data }) => {
      found.serverStatus = data.status;
      const st = $('recovery-server-status');
      if (S.recovery === found && st) st.textContent = data.status === 'recorded'
        ? 'Serveri andmetel on see tehing salvestatud – „Proovi uuesti“ näitab selle tšekki.'
        : 'Serveri andmetel pole seda tehingut (veel) salvestatud.';
    }).catch(() => {
      const st = $('recovery-server-status');
      if (S.recovery === found && st) st.textContent = 'Serverist ei saanud olekut kontrollida.';
    });
  }
}

function renderRecovery(c, note = '', cls = 'unconfirmed') {
  const found = S.recovery;
  c.replaceChildren();
  const box = el('div'); box.id = 'recovery';
  const bad = found.state === 'corrupt' || found.state === 'unreadable';
  const title = el('h2', null, bad ? 'Pooleli tehingu kirje on loetamatu'
    : found.rec.op === 'refund' ? 'Pooleli tagastus' : found.rec.op === 'cashmove' ? 'Pooleli sularahaliikumine' : 'Pooleli makse');
  title.id = 'dialog-title';
  box.append(title);
  if (bad) {
    box.append(stateBox('error', 'Seadmes on pooleli rahatehingu kirje, mida ei saa lugeda. Kontrolli juhi vaates tänaseid müüke ja tagastusi, enne kui kirjest loobud. Uut müüki enne seda ei tehta.'));
    if (note) box.append(stateBox(cls, note));
    // DEMO: this is NOT a verified manager authorization (that needs #2).
    const discard = el('button', 'secondary-btn', 'Loobu kirjest (DEMO – pole päris juhi kinnitus)'); discard.type = 'button'; discard.id = 'recover-discard';
    discard.addEventListener('click', () => {
      discard.remove();
      const sure = el('button', 'primary-btn', 'Kinnitan: loobu loetamatust kirjest'); sure.type = 'button'; sure.id = 'recover-discard-confirm';
      sure.addEventListener('click', () => {
        clearPending(null);
        if (readPending().state !== 'none') { renderRecovery(c, 'Kirjet ei õnnestunud kustutada: seadme salvestus ei tööta.', 'error'); return; }
        endRecovery();
      });
      box.append(sure);
    });
    box.append(discard);
  } else {
    const { rec } = found;
    const what = rec.op === 'cashmove' ? 'Kassa rahaliikumine ' + rec.body.direction.toUpperCase() + ' ' + formatEuro(rec.body.amount_cents) + '. Põhjus: ' + rec.body.reason_text
      : rec.op === 'billpay'
      ? `Lauaarve osamakse: ${METHOD_ET[rec.body.method]} ${formatEuro(rec.body.amount_cents)}.`
      : rec.op === 'sale'
      ? `${rec.body.lines ? `Kassamüük, ${rec.body.lines.length} rida` : 'Lauaarve tasumine'}. Kliendilt vastu võetud ${formatEuro(rec.body.tendered_cents)}.`
      : `Tagastus tšekist, ${rec.body.lines.length} rida. Põhjus: ${rec.body.reason}`;
    box.append(el('p', 'muted', `${what} Alustatud ${new Date(rec.createdAt).toLocaleString('et-EE', { timeZone: 'Europe/Tallinn' })}.`));
    box.append(stateBox(cls, note || 'Server võis selle tehingu juba salvestada. Ära võta raha uuesti vastu ega maksa välja. Vajuta „Proovi uuesti“ – sama tehingut ei registreerita kaks korda.'));
    // Informational only: the way out is still the same-key retry below.
    const st = el('p', 'muted', found.serverStatus === 'recorded' ? 'Serveri andmetel on see tehing salvestatud – „Proovi uuesti“ näitab selle tšekki.'
      : found.serverStatus === 'not_recorded' ? 'Serveri andmetel pole seda tehingut (veel) salvestatud.' : 'Kontrollin serverist tehingu olekut…');
    st.id = 'recovery-server-status';
    box.append(st);
    const retry = el('button', 'primary-btn', 'Proovi uuesti (sama tehing)'); retry.type = 'button'; retry.id = 'recover-retry';
    retry.addEventListener('click', () => recoverPending(retry));
    if (AUTH.mode === 'server' && rec.registerId !== S.registerId) {
      box.append(stateBox('error',
        'See pooleli tehing kuulub teisele kassale. Turvalisuse huvides ei saa seda siin uuesti saata. ' +
        'Ära võta raha teist korda vastu; palu juhil kontrollida algset kassat ja tehingu olekut.'));
    } else {
      box.append(retry);
    }
  }
  c.append(box);
}

async function recoverPending(btn) {
  const { rec } = S.recovery;
  btn.disabled = true; btn.textContent = 'Saadan…';
  // Same lock + same order as a normal operation: cleanup BEFORE journal delete.
  const cleanup = rec.op === 'sale' && rec.cartKey ? () => clearPaidCart(rec.cartKey) : null;
  const out = await runMoneyOperation(rec, cleanup);
  if (out.blocked) { renderRecovery($('modal-content'), `Ei saanud jätkata: ${out.blocked}.`); return; }
  if (out.data && rec.op === 'billpay') {
    S.recovery = null; $('close-modal').hidden = false;
    if (S.bill?.key === rec.key) S.bill = null;
    await billPaid(rec.path.split('/')[4], out.data, 'Pooleli makse lõpetati.');
    return;
  }
  if (out.data) {
    if (S.payment?.key === rec.key) S.payment = null;
    if (S.refund?.key === rec.key) S.refund = null;
    if (!out.cleaned) { renderRecovery($('modal-content'), CLEANUP_WARNING, 'error'); return; } // journal kept
    S.recovery = null; $('close-modal').hidden = false;
    if (rec.op === 'sale') await showReceipt(out.data); else if (rec.op === 'cashmove') await showCashMovementResult(out.data); else await showRefundResult(out.data);
    return;
  }
  if (isUncertain(out.err)) { renderRecovery($('modal-content'), `Tulemus on endiselt teadmata (${out.err.message}). Proovi hetke pärast uuesti.`); return; }
  S.recovery = null; $('close-modal').hidden = false; // proven not recorded; journal cleared
  $('modal-content').replaceChildren(stateBox('error', `Tehingut ei salvestatud: ${out.err.message}`));
  renderTotals();
}

function endRecovery() {
  S.recovery = null;
  $('close-modal').hidden = false;
  closeModal();
  renderTotals();
}

function checkPendingJournal() {
  if (S.recovery) return;
  const found = readPending();
  if (found.state === 'none') return;
  // Our own in-flight attempt in this tab is handled by its own dialog.
  if (found.state === 'ok' && [S.payment?.key, S.refund?.key, S.bill?.key, S.cashMove?.key].includes(found.rec.key)) return;
  // Recovery data can be stale or belong to an earlier provisioned register.
  // It must NEVER redirect a real cashier to another register.
  if (AUTH.mode !== 'server' && found.state === 'ok' && found.rec.registerId &&
      S.registers.some((r) => r.id === found.rec.registerId)) {
    S.registerId = found.rec.registerId; renderRegisterSelect(); refreshDrawer();
  }
  showRecovery(found);
}

// ---------------------------------------------------------------- server auth

/** Manager step-up fields: a DIFFERENT manager of this location + their PIN. */
function approvalFields(state, onChange) {
  const box = el('div', 'approval-box'); box.id = 'approval-box';
  box.append(el('strong', null, 'Juhi kinnitus'));
  const sel = el('select'); sel.id = 'approver-select'; sel.setAttribute('aria-label', 'Kinnitav juht');
  sel.append(el('option', null, '– vali juht –'));
  sel.firstChild.value = '';
  for (const st of AUTH.deviceInfo?.staff || []) {
    if (!st.can_approve || st.id === AUTH.me?.staff_id) continue;
    const o = el('option', null, st.display_name); o.value = st.id; sel.append(o);
  }
  sel.value = state.approverId;
  const pin = el('input'); pin.id = 'approver-pin'; pin.type = 'password'; pin.inputMode = 'numeric';
  pin.autocomplete = 'off'; pin.maxLength = 8; pin.setAttribute('aria-label', 'Juhi PIN');
  sel.addEventListener('change', () => { state.approverId = sel.value; state.approvalId = null; onChange(); });
  pin.addEventListener('input', () => { state.approverPin = pin.value; state.approvalId = null; onChange(); });
  box.append(sel, pin);
  return box;
}

function showAuth(build) {
  $('auth-screen').hidden = false;
  const c = $('auth-content'); c.replaceChildren();
  build(c);
  const f = c.querySelector('input, button'); if (f) f.focus();
}
function hideAuth() { $('auth-screen').hidden = true; }

function showEnroll(note = '') {
  showAuth((c) => {
    const h = el('h2', null, 'Seadme registreerimine'); h.id = 'auth-title';
    c.append(h, el('p', 'muted', 'Sisesta juhi loodud ühekordne registreerimiskood. Seade seotakse ettevõtte, asukoha ja kassaga.'));
    const input = el('input'); input.id = 'enroll-code'; input.autocomplete = 'off'; input.setAttribute('aria-label', 'Registreerimiskood');
    const msg = el('div', 'dialog-error', note); msg.setAttribute('role', 'alert');
    const ok = el('button', 'primary-btn', 'Registreeri seade'); ok.type = 'button'; ok.id = 'enroll-submit';
    ok.addEventListener('click', async () => {
      ok.disabled = true;
      try {
        const { data } = await api('/api/v1/auth/devices/enroll', { method: 'POST', body: { code: input.value.trim() } });
        AUTH.device = data.device_token; writeStore(localStorage, LS_DEVICE, AUTH.device);
        await showPinLogin();
      } catch (e) { msg.textContent = e.message; ok.disabled = false; }
    });
    c.append(input, msg, ok);
  });
}

async function showPinLogin(note = '') {
  try {
    AUTH.deviceInfo = (await api('/api/v1/auth/device')).data;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { // revoked or unknown device
      AUTH.device = null; writeStore(localStorage, LS_DEVICE, null); showEnroll('Seade pole registreeritud või registreering tühistati.');
    } else showAuth((c) => c.append(stateBox('error', `Serveriga ei saanud ühendust: ${e.message}`)));
    return;
  }
  const info = AUTH.deviceInfo;
  let chosen = null;
  showAuth((c) => {
    const h = el('h2', null, 'Sisselogimine'); h.id = 'auth-title';
    c.append(h, el('p', 'muted', `${info.tenant_name} · ${info.location_name}${info.register_name ? ` · ${info.register_name}` : ''} · ${info.device.name}`));
    if (note) c.append(stateBox('unconfirmed', note));
    const grid = el('div', 'staff-grid');
    for (const st of info.staff) {
      const b = el('button', 'staff-btn', st.display_name); b.type = 'button'; b.dataset.staff = st.id;
      b.addEventListener('click', () => { chosen = st.id; grid.querySelectorAll('.staff-btn').forEach((x) => x.classList.toggle('selected', x === b)); pin.focus(); });
      grid.append(b);
    }
    if (!info.staff.length) grid.append(el('p', 'muted', 'Selles asukohas pole PIN-iga töötajaid. Lisa need juhi vaates.'));
    const pin = el('input'); pin.id = 'pin-input'; pin.type = 'password'; pin.inputMode = 'numeric'; pin.autocomplete = 'off';
    pin.maxLength = 8; pin.setAttribute('aria-label', 'PIN');
    const msg = el('div', 'dialog-error'); msg.setAttribute('role', 'alert');
    const ok = el('button', 'primary-btn', 'Logi sisse'); ok.type = 'button'; ok.id = 'pin-submit';
    const submit = async () => {
      if (!chosen) { msg.textContent = 'Vali enda nimi.'; return; }
      ok.disabled = true;
      try {
        const { data } = await api('/api/v1/auth/pin-login', { method: 'POST', body: { staff_id: chosen, pin: pin.value } });
        AUTH.session = data.token; writeStore(sessionStorage, SS_SESSION, AUTH.session);
        pin.value = '';
        await afterLogin();
      } catch (e) {
        pin.value = ''; ok.disabled = false;
        msg.textContent = e instanceof ApiError && e.status === 429 ? 'Liiga palju valesid PIN-e. Oota veidi ja proovi uuesti.'
          : e instanceof ApiError && e.status === 401 ? 'Vale PIN.' : e.message;
      }
    };
    ok.addEventListener('click', submit);
    pin.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    pin.placeholder = 'PIN';
    // Large on-screen keypad for shared touch tills (the field still accepts a keyboard).
    const pad = el('div', 'pin-pad');
    for (const k of ['1', '2', '3', '4', '5', '6', '7', '8', '9', '⌫', '0', '✓']) {
      const kb = el('button', `pin-key${k === '✓' ? ' ok' : ''}`, k); kb.type = 'button';
      kb.setAttribute('aria-label', k === '⌫' ? 'Kustuta' : k === '✓' ? 'Logi sisse' : k);
      kb.addEventListener('click', () => {
        if (k === '⌫') pin.value = pin.value.slice(0, -1);
        else if (k === '✓') submit();
        else if (pin.value.length < 8) pin.value += k;
      });
      pad.append(kb);
    }
    c.append(grid, pin, pad, msg, ok);
  });
}

function sessionLost() {
  if (!AUTH.session) return;
  stopLive();
  AUTH.session = null; AUTH.me = null; writeStore(sessionStorage, SS_SESSION, null);
  showPinLogin('Sessioon lõppes (lukustus või aegumine). Logi uuesti sisse – pooleli tehing jätkub sama võtmega.');
}

async function lockTill() {
  stopLive();
  try { await api('/api/v1/auth/logout', { method: 'POST' }); } catch { /* the session ends anyway */ }
  AUTH.session = null; AUTH.me = null; writeStore(sessionStorage, SS_SESSION, null);
  await showPinLogin();
}

const ROLE_ET = { cashier: 'kassapidaja', waiter: 'teenindaja', kitchen: 'köök', manager: 'juht', owner: 'omanik' };
/** UI hint only; the server enforces every permission. */
const can = (perm) => AUTH.mode === 'demo' || Boolean(AUTH.me?.permissions?.includes(perm));

function renderIdentity() {
  const me = AUTH.me;
  const name = AUTH.mode === 'demo' ? 'DEMO kassapidaja' : me?.display_name || '';
  $('cashier-name').textContent = name;
  $('cashier-initials').textContent = name.split(/\s+/).map((x) => x[0] || '').join('').slice(0, 2).toUpperCase();
  $('mode-pill').textContent = AUTH.mode === 'demo' ? '● DEMO' : `● ${(me?.roles || []).map((r) => ROLE_ET[r] || r).join(', ')}`;
  $('lock-top').hidden = AUTH.mode !== 'server';
  $('lock-button').hidden = AUTH.mode !== 'server';
  // No register picker is ever presented to a real cashier, including
  // a misconfigured or unbound till that cannot open the sales workspace.
  const till = AUTH.mode === 'server';
  $('register-select').disabled = till;
  $('register-select').hidden = till;
  $('register-select').style.display = till ? 'none' : '';
  $('register-fixed-label').hidden = !till;
}

async function afterLogin() {
  AUTH.me = (await api('/api/v1/auth/me')).data;
  hideAuth();
  renderIdentity();
  await loadWorkspace();
}

// ------------------------------------------------------------------------ boot

async function selectRegister(id) {
  if (paymentLocked()) { $('register-select').value = String(S.registerId); announce('Pooleli makse: lõpeta see enne kassa vahetamist.'); return; }
  S.registerId = id;
  try { localStorage.setItem(LS_REGISTER, String(id)); } catch { /* ignore */ }
  S.quote = null; S.quoteSig = '';
  await loadProducts();
  await refreshDrawer();
  activateMode(S.mode);
}

/** The outlet's sellable products at its approved prices (P1-1a). Re-read on
 * register change and when the office publishes a price or product change. */
async function loadProducts() {
  const q = S.registerId ? `?register_id=${S.registerId}` : '';
  const { data } = await api(`/api/v1/products${q}`);
  S.products = data;
  S.quote = null; S.quoteSig = '';
}

// ------------------------------------------------------ offline limit (O1c)
// Only an edge till answers /api/v1/sync/status (404 centrally). The banner warns
// before the offline limit locks the till and explains a lock. Sales already
// made are never affected; they sync when the connection returns.

const OFFLINE_MSG = {
  offline_limit_exceeded: 'Kassa on lukus: keskserveriga pole ühendust olnud lubatud aja jooksul. Senised müügid on alles ja sünkroonitakse, kui ühendus taastub.',
  clock_moved_back: 'Kassa kell on tagasi keeratud – müük on peatatud. Paranda kassa kell.',
};
const OFFLINE_WARN_S = 12 * 3600;
const OFFLINE = { edge: null, timer: null };

function fmtLeft(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

async function refreshOfflineBanner() {
  if (OFFLINE.edge === false) return;
  const box = $('offline-banner');
  if (!box) return;
  let st;
  try {
    const res = await fetch('/api/v1/sync/status', { headers: authHeaders(), cache: 'no-store' });
    if (res.status === 404) { OFFLINE.edge = false; box.hidden = true; clearInterval(OFFLINE.timer); return; }
    if (!res.ok) return;                 // e.g. not logged in yet: keep the last state
    st = await res.json();
  } catch { return; }                    // no connection to the till's own server: keep the last state
  OFFLINE.edge = true;
  let text = '', kind = '';
  if (st.blocked) {
    kind = 'blocked';
    text = st.reason === 'never_connected' ? 'Kassa pole veel keskserveriga ühendunud. Ühenda kassa korra internetti, siis saab müüa.'
      : OFFLINE_MSG[st.reason] || OFFLINE_MSG.offline_limit_exceeded;
  } else if (st.remaining_s <= OFFLINE_WARN_S) {
    kind = 'warn';
    text = `Keskserveriga pole ühendust. Kassa lukustub ${fmtLeft(st.remaining_s)} pärast (piirang ${st.limit_hours} h), kui ühendus ei taastu. Müügid on alles.`;
  }
  box.hidden = !text;
  box.className = `offline-banner ${kind}`;
  box.textContent = text;
  box.dataset.reason = st.reason || (kind ? 'warning' : '');
}

function startOfflineBanner() {
  clearInterval(OFFLINE.timer);
  refreshOfflineBanner();
  OFFLINE.timer = setInterval(refreshOfflineBanner, 60_000);
}

async function boot() {
  try {
    AUTH.mode = (await api('/api/v1/auth/config')).data.mode;
  } catch (e) {
    $('view-root').replaceChildren(stateBox('error', `Serveriga ei saanud ühendust: ${e.message}`));
    const retry = el('button', 'primary-btn', 'Proovi uuesti'); retry.type = 'button'; retry.addEventListener('click', boot);
    $('view-root').append(retry);
    return;
  }
  if (AUTH.mode === 'demo') { renderIdentity(); await loadWorkspace(); return; }
  AUTH.device = readStore(localStorage, LS_DEVICE);
  AUTH.session = readStore(sessionStorage, SS_SESSION);
  if (!AUTH.device) { showEnroll(); return; }
  if (AUTH.session) {
    try {
      AUTH.deviceInfo = (await api('/api/v1/auth/device')).data;
      await afterLogin();
      return;
    } catch { /* fall through to PIN */ }
  }
  await showPinLogin();
}

async function loadWorkspace() {
  startOfflineBanner();
  const root = $('view-root');
  root.replaceChildren(el('p', 'muted-p', 'Laadin tooteid ja kassasid serverist…'));
  try {
    const [{ data: products }, { data: registers }] = await Promise.all([api('/api/v1/products'), api('/api/v1/registers')]);
    S.products = products; S.registers = registers;
  } catch (e) {
    root.replaceChildren(stateBox('error', `Serveriga ei saanud ühendust: ${e.message}`));
    const retry = el('button', 'primary-btn', 'Proovi uuesti'); retry.type = 'button'; retry.addEventListener('click', loadWorkspace);
    root.append(retry);
    return;
  }
  if (!S.registers.length) { root.replaceChildren(stateBox('error', 'Sellel ettevõttel pole ühtegi kassat.')); return; }
  let saved = null;
  try { saved = Number(localStorage.getItem(LS_REGISTER)); } catch { /* ignore */ }
  const bound = AUTH.me?.register_id;
  if (AUTH.mode === 'server') {
    if (!Number.isInteger(bound) || !S.registers.some((r) => r.id === bound)) {
      root.replaceChildren(stateBox('error',
        'Kassa ei ole sellele seadmele õigesti määratud. Teenindaja ei saa kassat valida. ' +
        'Palu juhil kontrollida seadme sidumist AXAR OFFICE-is.'));
      return;
    }
    S.registerId = bound; // ONLY the server-authenticated device controls the till.
  } else {
    S.registerId = S.registers.some((r) => r.id === saved) ? saved : S.registers[0].id;
  }
  await loadProducts();
  renderRegisterSelect();
  renderIdentity();
  S.storageOk = storageWorks();
  S.locksOk = Boolean(globalThis.navigator?.locks && typeof navigator.locks.request === 'function');
  await refreshDrawer();
  activateMode(S.mode || 'express');
  checkPendingJournal(); // crash/reload recovery before any new sale
  startLive();
}

document.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => activateMode(b.dataset.mode)));
$('register-select').addEventListener('change', (e) => selectRegister(Number(e.target.value)));
// Tables: one-shot cash stays the quick path; a partially paid bill (or a
// pending tender) always continues in the split-payment dialog.
const startPayment = () => (S.mode === 'tables' && (S.order?.status === 'partially_paid' || S.bill?.state === 'unconfirmed')
  ? openBillPayment() : openPayment());
$('split-button').addEventListener('click', openBillPayment);
$('pay-button').addEventListener('click', startPayment);
$('lock-button').addEventListener('click', lockTill);
$('lock-top').addEventListener('click', lockTill);
$('mini-pay').addEventListener('click', () => {
  if (!$('pay-button').disabled) startPayment();
  else $('checkout-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
$('mini-view').addEventListener('click', () => $('checkout-panel').scrollIntoView({ behavior: 'smooth', block: 'start' }));
$('send-button').addEventListener('click', sendToKitchen);
$('close-modal').addEventListener('click', closeModal);
$('modal').addEventListener('click', (e) => { if (e.target === $('modal')) closeModal(); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('modal').hidden) closeModal();
  if (e.key === '/' && $('modal').hidden) {
    const input = $('product-search');
    if (input && document.activeElement !== input) { e.preventDefault(); input.focus(); }
  }
});
// Another tab started or finished a money operation.
window.addEventListener('storage', (e) => {
  if (e.key !== LS_PENDING) return;
  if (e.newValue == null) {
    // Another tab settled the operation (confirmed, or proven not recorded).
    // Reload the server's view instead of trusting our memory.
    if (S.recovery) endRecovery();
    reloadOrder().then(() => { if (S.mode === 'tables') refreshTables(); refreshTableView(); });
    refreshDrawer();
    return;
  }
  checkPendingJournal();
});
window.addEventListener('offline', () => setOnline(false));
window.addEventListener('online', () => { setOnline(true); refreshDrawer(); });
$('local-clock').textContent = new Intl.DateTimeFormat('et-EE', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Tallinn' }).format(new Date());
boot();
