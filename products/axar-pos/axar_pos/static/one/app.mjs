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

// DEMO identity. These headers are NOT authentication (see RFC-0002 / #2).
const IDENTITY = { 'X-Tenant-ID': 'demo', 'X-Cashier-ID': 'kassa1' };
const DEMO_MANAGER = { 'X-Role': 'manager' };
const TIMEOUT_MS = 8000;
const LS_REGISTER = 'axar.one.register';
// Crash-safe journal of the ONE in-flight money operation (sale or refund).
// Written BEFORE the POST, cleared only after a definitive server answer.
const LS_PENDING = 'axar.one.pending.v1';
const PENDING_PATHS = [/^\/api\/v1\/sales\/cash$/, /^\/api\/v1\/sales\/[0-9a-f-]{36}\/refunds$/,
  /^\/api\/v1\/orders\/[0-9a-f-]{36}\/pay-cash$/];
const ICONS = { Kohv: ['☕', 'sand'], Toit: ['🥐', 'rose'], Joogid: ['💧', 'sky'], Muud: ['📦', 'lilac'] };

// ------------------------------------------------------------------ HTTP layer

class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}
class NetworkError extends Error {}

async function api(path, { method = 'GET', body, headers = {} } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { ...IDENTITY, ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
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
    const msg = (data && (data.message || (typeof data.detail === 'string' ? data.detail : null))) || `Viga ${res.status}`;
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
const DEFINITIVE_CODES = new Set(['no_open_drawer', 'insufficient_tender', 'insufficient_drawer_cash',
  'over_refund', 'invalid_refund', 'not_found', 'order_changed']);
const isDefinitive = (e) => e instanceof ApiError && (e.status === 422 || (DEFINITIVE_CODES.has(e.code) && [404, 409, 422].includes(e.status)));
const isUncertain = (e) => !isDefinitive(e);

// ----------------------------------------------------------------------- state

const S = {
  products: [], registers: [], registerId: null, drawer: null,
  mode: 'express', category: 'Kõik',
  tables: [], tablesError: '', tableId: null, order: null, // server-side table bills
  orderBusy: false, orderQueue: Promise.resolve(), orderGen: 0, orderMsg: { text: '', cls: '' },
  tickets: [], ticketsError: '', kitchenPending: null, pollTimer: null, pollBusy: false,
  walkin: {},                 // registerId -> {productId: qty}, memory only
  quote: null, quoteSig: '', quoteError: '', quoteSeq: 0, quoteTimer: null,
  payment: null,              // pending/last payment attempt
  refund: null,               // pending refund attempt
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
  if (!b || !isInt(b.register_id, 1, Number.MAX_SAFE_INTEGER) || rec.registerId !== b.register_id) return false;
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
  if (rec.op === 'refund') {
    if (!okLines()) return false;
    return typeof rec.saleId === 'string' && UUID_RE.test(rec.saleId)
      && rec.path === `/api/v1/sales/${rec.saleId}/refunds` && onlyKeys(b, ['register_id', 'lines', 'reason'])
      && typeof b.reason === 'string' && b.reason.length >= 3 && b.reason.length <= 500
      && b.lines.every((l) => onlyKeys(l, ['line_no', 'quantity']) && isInt(l.line_no, 1, 10_000) && isInt(l.quantity, 1, 999))
      && onlyKeys(rec.headers || {}, ['X-Role']);
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
  pill.textContent = on ? '● Server ühendatud' : '● Ühendus puudub';
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
      const { data } = await api('/api/v1/sales/quote', { method: 'POST', body: { lines: requestLines(lines) } });
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
  const pay = $('pay-button');
  const resuming = S.payment?.state === 'unconfirmed' && S.payment.sig === cartSig(lines);
  const orderReady = S.mode !== 'tables' || (S.order && !S.orderBusy);
  pay.disabled = !S.storageOk || !S.locksOk || Boolean(S.recovery) || (!(S.drawer && ready && lines.length && orderReady) && !resuming);
  pay.firstChild.textContent = resuming ? 'Lõpeta pooleli makse ' : 'Võta sularaha ';
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
    box.append(el('span', null, `Vahetus #${d.id} avatud · ${d.sale_count} müüki · oodatav sularaha ${formatEuro(d.expected_cents)}`));
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

const IDENTITY_NOTE = 'Ettevõte ja kassapidaja on DEMO päised, mitte sisselogimine (RFC-0002).';

function renderTables() {
  const root = $('view-root'); root.replaceChildren();
  const section = el('section');
  section.append(el('div', 'demo-banner', `Lauaarved ja köögipiletid on serveris: sama arvet näeb iga selle ettevõtte seade. ${IDENTITY_NOTE}`));
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
    b.append(el('strong', null, tb.name), el('small', null, count ? `Arve avatud · ${count} tk` : 'Vaba'));
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
  if (tb) tb.open_order = { id: S.order.id, version: S.order.version, item_count: S.order.lines.reduce((n, l) => n + l.quantity, 0) };
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
    if (data.status !== 'open') {
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
  root.append(el('div', 'demo-banner', `Köögipiletid tulevad serverist ja uuenevad iga ${POLL_MS / 1000} sekundi järel. ${IDENTITY_NOTE}`));
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
    const head = el('div', 'ticket-head'); head.append(el('strong', null, `${tk.table_name} · #${tk.ticket_no}`), el('small', null, done ? 'VALMIS' : 'VALMIB'));
    const list = el('ul', 'ticket-list');
    for (const it of tk.lines) list.append(el('li', null, `${it.quantity} × ${it.name}`));
    const b = el('button', `ticket-ready${done ? ' done' : ''}`, done ? 'Valmis ✓' : 'Märgi valmis'); b.type = 'button';
    b.disabled = done;
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
function startPolling() {
  clearInterval(S.pollTimer);
  S.pollTimer = ['tables', 'kitchen'].includes(S.mode) ? setInterval(poll, POLL_MS) : null;
}
async function poll() {
  if (document.hidden || S.pollBusy) return;
  S.pollBusy = true;
  try {
    if (S.mode === 'tables') await refreshTables();
    else if (S.mode === 'kitchen') await loadTickets();
  } finally { S.pollBusy = false; }
}

async function renderManager() {
  const root = $('view-root'); root.replaceChildren();
  root.append(el('div', 'demo-banner', 'Tagastus kasutab DEMO juhi rolli (päis), mitte päris autentimist. Päris sisselogimine tuleb issue #2-ga.'));
  const stats = el('div', 'stats-grid'); stats.id = 'report';
  const actions = el('div', 'manager-actions');
  const close = el('button', 'secondary-btn', 'Sulge vahetus ja loe raha'); close.type = 'button'; close.id = 'close-drawer';
  close.disabled = !S.drawer;
  close.addEventListener('click', closeDrawerDialog);
  actions.append(close);
  const salesBox = el('section'); salesBox.id = 'sales-list';
  root.append(stats, actions, salesBox);
  stats.append(el('p', 'muted-p', 'Laadin aruannet…'));
  try {
    const { data: rep } = await api('/api/v1/reports/daily');
    if (S.mode !== 'manager') return;
    stats.replaceChildren();
    for (const [label, value, note] of [
      ['Müük', formatEuro(rep.total_cents), `${rep.sale_count} tšekki`],
      ['Tagastused', formatEuro(rep.refund_total_cents), `${rep.refund_count} tagastust`],
      ['Sularaha neto', formatEuro(rep.net_cash_cents), 'laekunud − tagastatud'],
      ['Käibemaks neto', formatEuro(rep.net_vat_cents), rep.business_date],
    ]) {
      const d = el('div', 'stat'); d.append(el('small', null, label), el('strong', null, value), el('p', null, note)); stats.append(d);
    }
    const { data: sales } = await api(`/api/v1/sales?business_date=${encodeURIComponent(rep.business_date)}`);
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
    stats.replaceChildren(el('div', 'pay-state error', `Aruannet ei saanud laadida: ${e.message}`));
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
  if (S.payment?.state === 'sending' || S.refund?.state === 'sending') return; // never abandon an in-flight request
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
      confirm.addEventListener('click', async () => {
        confirm.disabled = true;
        try {
          const { data } = await api(`/api/v1/drawer/${d.id}/close`, { method: 'POST', body: { counted_cents: counted } });
          await refreshDrawer();
          c.replaceChildren(title, stateBox(data.variance_cents === 0 ? 'ok' : 'unconfirmed',
            `Kassa suletud. Oodatud ${formatEuro(data.expected_cents)}, loetud ${formatEuro(data.counted_cents)}, vahe ${formatEuro(data.variance_cents)}.`));
          const done = el('button', 'primary-btn', 'Valmis'); done.type = 'button'; done.addEventListener('click', closeModal);
          c.append(done);
          if (S.mode === 'manager') renderManager();
        } catch (e) { err.textContent = e.message; confirm.disabled = false; }
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
  try { writePending({ ...rec, registerId: rec.body.register_id }); } catch {
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
  c.append(title, el('p', 'muted', `Tšekk nr ${sale.receipt_no} · server kinnitas makse.`), change);
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
  S.refund = { saleId, sale, qty: {}, reason: '', key: null, bodySig: null, state: 'input', error: '', result: null };
  openModal(renderRefund);
}

function renderRefund(c) {
  const r = S.refund;
  c.replaceChildren();
  const title = el('h2', null, `Tagastus · tšekk #${r.sale.receipt_no}`); title.id = 'dialog-title';
  c.append(title, el('p', 'muted', 'Simuleeritud sularaha tagastus. Juhi roll on DEMO (päis), mitte päris autentimine.'));
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
  if (r.state === 'sending') c.append(stateBox('busy', 'Saadan tagastust serverisse…'));
  if (r.state === 'unconfirmed') c.append(stateBox('unconfirmed', `Tagastuse olek on teadmata (${r.error}). Ära maksa raha uuesti välja – vajuta „Proovi uuesti“.`));
  if (r.state === 'error') c.append(stateBox('error', `Tagastust ei salvestatud: ${r.error}`));
  const ok = el('button', 'primary-btn', r.state === 'unconfirmed' ? 'Proovi uuesti (sama tagastus)' : 'Kinnita tagastus'); ok.type = 'button'; ok.id = 'confirm-refund';
  const valid = () => r.state === 'unconfirmed' || (Object.values(r.qty).some((q) => q > 0) && r.reason.trim().length >= 3);
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
  } else if (!r.key || r.bodySig !== bodySig) { r.key = newKey(); r.bodySig = bodySig; }
  const retry = r.state === 'unconfirmed';
  r.state = 'sending'; r.error = '';
  renderRefund($('modal-content'));
  const rec = { op: 'refund', key: r.key, path: `/api/v1/sales/${r.saleId}/refunds`, body, headers: DEMO_MANAGER, saleId: r.saleId, createdAt: new Date().toISOString() };
  const out = await runMoneyOperation(rec, null);
  if (out.blocked) {
    r.error = out.blocked;
    if (retry) r.state = 'unconfirmed'; else { r.state = 'error'; r.key = null; r.bodySig = null; }
  } else if (out.data) {
    r.state = 'done'; r.result = out.data;
    await showRefundResult(out.data);
    return;
  } else if (isUncertain(out.err)) { r.state = 'unconfirmed'; r.error = out.err.message; } // journal stays
  else { r.state = 'error'; r.error = out.err.message; r.key = null; r.bodySig = null; }
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
    : found.rec.op === 'sale' ? 'Pooleli makse' : 'Pooleli tagastus');
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
    const what = rec.op === 'sale'
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
    box.append(retry);
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
  if (out.data) {
    if (S.payment?.key === rec.key) S.payment = null;
    if (S.refund?.key === rec.key) S.refund = null;
    if (!out.cleaned) { renderRecovery($('modal-content'), CLEANUP_WARNING, 'error'); return; } // journal kept
    S.recovery = null; $('close-modal').hidden = false;
    if (rec.op === 'sale') await showReceipt(out.data); else await showRefundResult(out.data);
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
  if (found.state === 'ok' && (S.payment?.key === found.rec.key || S.refund?.key === found.rec.key)) return;
  if (found.state === 'ok' && found.rec.registerId && S.registers.some((r) => r.id === found.rec.registerId)) {
    S.registerId = found.rec.registerId; renderRegisterSelect(); refreshDrawer();
  }
  showRecovery(found);
}

// ------------------------------------------------------------------------ boot

async function selectRegister(id) {
  if (paymentLocked()) { $('register-select').value = String(S.registerId); announce('Pooleli makse: lõpeta see enne kassa vahetamist.'); return; }
  S.registerId = id;
  try { localStorage.setItem(LS_REGISTER, String(id)); } catch { /* ignore */ }
  S.quote = null; S.quoteSig = '';
  await refreshDrawer();
  activateMode(S.mode);
}

async function boot() {
  const root = $('view-root');
  root.replaceChildren(el('p', 'muted-p', 'Laadin tooteid ja kassasid serverist…'));
  try {
    const [{ data: products }, { data: registers }] = await Promise.all([api('/api/v1/products'), api('/api/v1/registers')]);
    S.products = products; S.registers = registers;
  } catch (e) {
    root.replaceChildren(stateBox('error', `Serveriga ei saanud ühendust: ${e.message}`));
    const retry = el('button', 'primary-btn', 'Proovi uuesti'); retry.type = 'button'; retry.addEventListener('click', boot);
    root.append(retry);
    return;
  }
  if (!S.registers.length) { root.replaceChildren(stateBox('error', 'Sellel ettevõttel pole ühtegi kassat.')); return; }
  let saved = null;
  try { saved = Number(localStorage.getItem(LS_REGISTER)); } catch { /* ignore */ }
  S.registerId = S.registers.some((r) => r.id === saved) ? saved : S.registers[0].id;
  renderRegisterSelect();
  S.storageOk = storageWorks();
  S.locksOk = Boolean(globalThis.navigator?.locks && typeof navigator.locks.request === 'function');
  await refreshDrawer();
  activateMode('express');
  checkPendingJournal(); // crash/reload recovery before any new sale
}

document.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => activateMode(b.dataset.mode)));
$('register-select').addEventListener('change', (e) => selectRegister(Number(e.target.value)));
$('pay-button').addEventListener('click', openPayment);
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
